import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Literal, Optional

from app.core.database import get_db
from app.core.config import settings
from app.core.email import send_todo_created_email
from app.domains.todos.schemas import (
    TodoCreate,
    TodoPageResponse,
    TodoUpdate,
    TodoResponse,
)
from app.domains.todos import service as todo_service
from app.domains.todos.websocket import todo_connection_manager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/todos", tags=["Todos"])

@router.post("", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
async def create_todo(
    payload: TodoCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    todo = await todo_service.create_todo(db, payload)
    todo_response = TodoResponse.model_validate(todo)
    await todo_connection_manager.broadcast(
        {
            "event": "todo_created",
            "todo": todo_response.model_dump(mode="json"),
        }
    )
    background_tasks.add_task(
        send_todo_created_email,
        todo.id,
        todo.title,
        settings.TODO_NOTIFICATION_EMAIL,
    )
    logger.info("Queued todo creation email notification for todo_id=%s", todo.id)
    return todo


@router.websocket("/ws")
async def todos_websocket(websocket: WebSocket):
    await todo_connection_manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        todo_connection_manager.disconnect(websocket)

@router.get("", response_model=TodoPageResponse)
async def list_todos(
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = None,
    status_filter: Optional[Literal["pending", "completed"]] = Query(
        default=None, alias="status"
    ),
    completed: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    if (
        status_filter is not None
        and completed is not None
        and (status_filter == "completed") != completed
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="status and completed filters conflict",
        )
    try:
        return await todo_service.get_all_todos(
            db,
            limit=limit,
            cursor=cursor,
            status=status_filter,
            completed=completed,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

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
    return await todo_service.update_todo(db, todo, payload)

@router.delete("/{todo_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_todo(todo_id: int, db: AsyncSession = Depends(get_db)):
    todo = await todo_service.get_todo_by_id(db, todo_id)
    if not todo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Todo not found")
    await todo_service.delete_todo(db, todo)
    return None
