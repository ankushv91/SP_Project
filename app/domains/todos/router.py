from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional

from app.core.database import get_db
from app.domains.todos.schemas import TodoCreate, TodoUpdate, TodoResponse
from app.domains.todos import service as todo_service

router = APIRouter(prefix="/todos", tags=["Todos"])

@router.post("", response_model=TodoResponse, status_code=status.HTTP_201_CREATED)
async def create_todo(payload: TodoCreate, db: AsyncSession = Depends(get_db)):
    return await todo_service.create_todo(db, payload)

@router.get("", response_model=List[TodoResponse])
async def list_todos(
    skip: int = 0,
    limit: int = 100,
    completed: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    return await todo_service.get_all_todos(db, skip=skip, limit=limit, completed=completed)

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
