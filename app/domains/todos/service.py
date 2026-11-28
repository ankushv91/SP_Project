import base64
import binascii
import json
import logging
from datetime import datetime
from typing import Literal, Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import and_, or_, select
from redis.exceptions import RedisError
from pydantic import ValidationError

from app.core.redis import get_redis_client
from app.domains.todos.models import Todo
from app.domains.todos.schemas import (
    TodoCreate,
    TodoUpdate,
    TodoPageResponse,
    TodoResponse,
)

logger = logging.getLogger(__name__)

CACHE_TTL = 60
CACHE_KEY_TODOS = "todos:list"


def _todo_list_cache_key(
    limit: int, cursor: Optional[str], completed: Optional[bool]
) -> str:
    return f"{CACHE_KEY_TODOS}:{limit}:{cursor}:{completed}"


def _encode_todo_cursor(todo: Todo) -> str:
    cursor_data = {
        "created_at": todo.created_at.isoformat(),
        "id": todo.id,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(cursor_data, separators=(",", ":")).encode("utf-8")
    )
    return encoded.decode("ascii").rstrip("=")


def _decode_todo_cursor(cursor: str) -> tuple[datetime, int]:
    try:
        padded_cursor = cursor + "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            padded_cursor, altchars=b"-_", validate=True
        )
        cursor_data = json.loads(decoded)
        created_at = datetime.fromisoformat(cursor_data["created_at"])
        todo_id = cursor_data["id"]
        if not isinstance(todo_id, int) or isinstance(todo_id, bool) or todo_id < 1:
            raise ValueError("Cursor todo ID must be a positive integer")
        return created_at, todo_id
    except (
        binascii.Error,
        UnicodeDecodeError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise ValueError("Invalid todo cursor") from exc


async def invalidate_todos_cache() -> None:
    redis_client = get_redis_client()
    try:
        keys = [
            key
            async for key in redis_client.scan_iter(match=f"{CACHE_KEY_TODOS}:*")
        ]
        if keys:
            await redis_client.delete(*keys)
    except RedisError:
        logger.warning("Failed to invalidate the todo list cache", exc_info=True)

async def get_all_todos(
    db: AsyncSession,
    limit: int = 50,
    cursor: Optional[str] = None,
    status: Optional[Literal["pending", "completed"]] = None,
    completed: Optional[bool] = None,
) -> TodoPageResponse:
    redis_client = get_redis_client()
    completed_filter = (
        status == "completed" if status is not None else completed
    )
    cache_key = _todo_list_cache_key(limit, cursor, completed_filter)
    try:
        cached_todos = await redis_client.get(cache_key)
    except RedisError:
        logger.warning("Failed to read the todo list cache", exc_info=True)
        cached_todos = None

    if cached_todos is not None:
        try:
            return TodoPageResponse.model_validate_json(cached_todos)
        except (json.JSONDecodeError, TypeError, ValidationError):
            logger.warning("Discarding invalid cached todo list", exc_info=True)
            await invalidate_todos_cache()

    query = select(Todo)
    if completed_filter is not None:
        query = query.where(Todo.is_completed == completed_filter)
    if cursor is not None:
        _, cursor_id = _decode_todo_cursor(cursor)
        # Compare against the stored value to preserve SQLite timestamp precision.
        cursor_created_at = (
            select(Todo.created_at)
            .where(Todo.id == cursor_id)
            .scalar_subquery()
        )
        cursor_exists = select(Todo.id).where(Todo.id == cursor_id).exists()
        query = query.where(
            or_(
                and_(
                    cursor_exists,
                    or_(
                        Todo.created_at < cursor_created_at,
                        and_(
                            Todo.created_at == cursor_created_at,
                            Todo.id < cursor_id,
                        ),
                    ),
                ),
                and_(~cursor_exists, Todo.id < cursor_id),
            )
        )
    query = query.order_by(Todo.created_at.desc(), Todo.id.desc()).limit(limit + 1)
    result = await db.execute(query)
    todos = result.scalars().all()
    has_more = len(todos) > limit
    page_todos = todos[:limit]
    page = TodoPageResponse(
        items=[TodoResponse.model_validate(todo) for todo in page_todos],
        next_cursor=_encode_todo_cursor(page_todos[-1]) if has_more else None,
    )
    try:
        await redis_client.setex(
            cache_key,
            CACHE_TTL,
            page.model_dump_json(),
        )
    except RedisError:
        logger.warning("Failed to write the todo list cache", exc_info=True)
    return page

async def get_todo_by_id(db: AsyncSession, todo_id: int) -> Optional[Todo]:
    result = await db.execute(select(Todo).where(Todo.id == todo_id))
    return result.scalar_one_or_none()

async def create_todo(db: AsyncSession, todo_in: TodoCreate) -> Todo:
    todo = Todo(
        title=todo_in.title,
        description=todo_in.description,
        is_completed=todo_in.is_completed,
    )
    db.add(todo)
    await db.commit()
    await db.refresh(todo)
    await invalidate_todos_cache()
    return todo

async def update_todo(db: AsyncSession, todo: Todo, todo_in: TodoUpdate) -> Todo:
    update_data = todo_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(todo, field, value)
    await db.commit()
    await db.refresh(todo)
    await invalidate_todos_cache()
    return todo

async def delete_todo(db: AsyncSession, todo: Todo) -> None:
    await db.delete(todo)
    await db.commit()
    await invalidate_todos_cache()
