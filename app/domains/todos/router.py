import json
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_db
from app.core.config import settings
from app.core.email import send_todo_created_email
from app.core.redis import redis_client
from app.domains.todos.schemas import TodoCreate, TodoUpdate, TodoResponse
from app.domains.todos import service as todo_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/todos", tags=["Todos"])
TODO_LIST_CACHE_PREFIX = "todos:list"
TODO_LIST_CACHE_TTL_SECONDS = 60


def _todo_list_cache_key(skip: int, limit: int, completed: Optional[bool]) -> str:
    return f"{TODO_LIST_CACHE_PREFIX}:{skip}:{limit}:{completed}"


async def _invalidate_todo_list_cache() -> None:
    try:
        keys = [
            key
            async for key in redis_client.scan_iter(
                match=f"{TODO_LIST_CACHE_PREFIX}:*"
            )
        ]
        if keys:
            await redis_client.delete(*keys)
    except RedisError:
        logger.warning("Failed to invalidate the todo list cache", exc_info=True)

@router.post("", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    todo = await todo_service.create_todo(db, payload)
    await _invalidate_todo_list_cache()
    background_tasks.add_task(
        send_todo_created_email,
        todo.id,
        todo.title,
        settings.TODO_NOTIFICATION_EMAIL,
    )
    logger.info("Queued todo creation email notification for todo_id=%s", todo.id)
    return todo

@router.get("", response_model=List[TodoResponse])
async def list_todos(
    skip: int = 0,
    limit: int = 100,
    completed: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    cache_key = _todo_list_cache_key(skip, limit, completed)
    try:
        cached_todos = await redis_client.get(cache_key)
    except RedisError:
        logger.warning("Failed to read the todo list cache", exc_info=True)
        cached_todos = None

    if cached_todos is not None:
        try:
            return json.loads(cached_todos)
        except json.JSONDecodeError:
            logger.warning("Discarding invalid cached todo list for key=%s", cache_key)
            await _invalidate_todo_list_cache()

    todos = await todo_service.get_all_todos(
        db, skip=skip, limit=limit, completed=completed
    )
    serialized_todos = json.dumps(
        [
            TodoResponse.model_validate(todo).model_dump(mode="json")
            for todo in todos
        ]
    )
    try:
        await redis_client.set(
            cache_key, serialized_todos, ex=TODO_LIST_CACHE_TTL_SECONDS
        )
    except RedisError:
        logger.warning("Failed to write the todo list cache", exc_info=True)
    return todos

@router.get("/{todo_id}", response_model=TodoResponse)
async def get_todo(todo_id: int, db: AsyncSession = Depends(get_db)):
    todo = await todo_service.get_todo_by_id(db, todo_id)
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    return todo

@router.put("/{todo_id}", response_model=TodoResponse)
async def update_todo(todo_id: int, payload: TodoUpdate, db: AsyncSession = Depends(get_db)):
    todo = await todo_service.get_todo_by_id(db, todo_id)
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    updated_todo = await todo_service.update_todo(db, todo, payload)
    await _invalidate_todo_list_cache()
    return updated_todo

@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: int, db: AsyncSession = Depends(get_db)):
    todo = await todo_service.get_todo_by_id(db, todo_id)
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    await todo_service.delete_todo(db, todo)
    await _invalidate_todo_list_cache()
    return None
