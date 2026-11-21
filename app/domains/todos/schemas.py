from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class TodoBase(BaseModel):
    title: str
    description: Optional[str] = None
    is_completed: bool = False

class TodoCreate(TodoBase):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Buy groceries",
                    "description": "Pick up milk and bread",
                    "is_completed": False,
                }
            ]
        }
    )

class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = None

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "title": "Buy groceries",
                    "is_completed": True,
                }
            ]
        }
    )

class TodoResponse(TodoBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "title": "Buy groceries",
                    "description": "Pick up milk and bread",
                    "is_completed": False,
                    "created_at": "2026-01-15T10:30:00Z",
                    "updated_at": "2026-01-15T10:30:00Z",
                }
            ]
        },
    )
