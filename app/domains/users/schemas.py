from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional

class UserCreate(BaseModel):
    username: str
    email: str
    password: str

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "username": "jane.doe",
                    "email": "jane@example.com",
                    "password": "example-password-not-for-use",
                }
            ]
        }
    )

class UserResponse(BaseModel):
    id: int
    username: str
    email: str
    created_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "examples": [
                {
                    "id": 1,
                    "username": "jane.doe",
                    "email": "jane@example.com",
                    "created_at": "2026-01-15T10:30:00Z",
                }
            ]
        },
    )

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "access_token": "example.jwt.token",
                    "token_type": "bearer",
                }
            ]
        }
    )

class TokenPayload(BaseModel):
    sub: Optional[str] = None
