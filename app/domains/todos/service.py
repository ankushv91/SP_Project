import json
import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from redis.exceptions import RedisError
from typing import List, Optional
from pydantic import ValidationError

from app.core.redis import get_redis_client
from app.domains.todos.models import Todo
from app.domains.todos.schemas import TodoCreate, TodoUpdate, TodoResponse

logger = logging.getLogger(__name__)

CACHE_TTL = 60
CACHE_KEY_TODOS = "todos:list"


def _todo_list_cache_key(
    skip: int, limit: int, completed: Optional[bool]
) -> str:
    return f"{CACHE_KEY_TODOS}:{skip}:{limit}:{completed}"


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
    skip: int = 0,
    limit: int = 100,
    completed: Optional[bool] = None
) -> List[TodoResponse]:
    redis_client = get_redis_client()
    cache_key = _todo_list_cache_key(skip, limit, completed)
    try:
        cached_todos = await redis_client.get(cache_key)
    except RedisError:
        logger.warning("Failed to read the todo list cache", exc_info=True)
        cached_todos = None

    if cached_todos is not None:
        try:
            return [
                TodoResponse.model_validate(todo)
                for todo in json.loads(cached_todos)
            ]
        except (json.JSONDecodeError, TypeError, ValidationError):
            logger.warning("Discarding invalid cached todo list", exc_info=True)
            await invalidate_todos_cache()

    query = select(Todo).offset(skip).limit(limit)
    if completed is not None:
        query = query.where(Todo.is_completed == completed)
    result = await db.execute(query)
    todos = [
        TodoResponse.model_validate(todo)
        for todo in result.scalars().all()
    ]
    try:
        await redis_client.setex(
            cache_key,
            CACHE_TTL,
            json.dumps([todo.model_dump(mode="json") for todo in todos]),
        )
    except RedisError:
        logger.warning("Failed to write the todo list cache", exc_info=True)
    return todos

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
