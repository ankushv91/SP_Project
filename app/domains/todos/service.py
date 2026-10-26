from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List, Optional
from app.domains.todos.models import Todo
from app.domains.todos.schemas import TodoCreate, TodoUpdate

async def get_all_todos(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    completed: Optional[bool] = None
) -> List[Todo]:
    query = select(Todo).offset(skip).limit(limit)
    if completed is not None:
        query = query.where(Todo.is_completed == completed)
    result = await db.execute(query)
    return list(result.scalars().all())

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
    return todo

async def update_todo(db: AsyncSession, todo: Todo, todo_in: TodoUpdate) -> Todo:
    update_data = todo_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(todo, field, value)
    await db.commit()
    await db.refresh(todo)
    return todo

async def delete_todo(db: AsyncSession, todo: Todo) -> None:
    await db.delete(todo)
    await db.commit()
