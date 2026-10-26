from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class TodoBase(BaseModel):
    title: str
    description: Optional[str] = None
    is_completed: bool = False

class TodoCreate(TodoBase):
    pass

class TodoUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = None

class TodoResponse(TodoBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
