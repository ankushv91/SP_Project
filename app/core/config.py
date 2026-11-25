import logging
from functools import lru_cache
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

COMPOSE_ONLY_SETTINGS = frozenset(
    {
        "POSTGRES_DB",
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "API_PORT",
        "REDIS_PORT",
        "DOCS_PORT",
        "DOCS_AUTH_USER",
        "DOCS_AUTH_PASSWORD",
        "MAILHOG_UI_PORT",
        "AWS_PROFILE",
        "AWS_CONFIG_DIR",
        "ELASTIC_VERSION",
        "ELASTICSEARCH_PORT",
        "KIBANA_PORT",
        "KIBANA_ENCRYPTION_KEY",
    }
)


class Settings(BaseSettings):
    PROJECT_NAME: str = "SP Project API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    DATABASE_URL: str = "sqlite+aiosqlite:///./test.db"

    REDIS_URL: str = "redis://redis:6379/0"

    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 1025
    SMTP_FROM_EMAIL: str = "no-reply@example.com"
    TODO_NOTIFICATION_EMAIL: str = "todo-notifications@example.com"

    AWS_SECRET_NAME: str = "sp-project/jwt-secret"
    AWS_REGION: str | None = None

    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    @model_validator(mode="before")
    @classmethod
    def warn_about_unexpected_settings(cls, values: Any) -> Any:
        if isinstance(values, dict):
            valid_names = {name.casefold() for name in cls.model_fields}
            unexpected_names = sorted(
                name.upper()
                for name in values
                if isinstance(name, str)
                and name.casefold() not in valid_names
                and name.upper() not in COMPOSE_ONLY_SETTINGS
            )
            if unexpected_names:
                logger.warning(
                    "Unexpected settings keys: %s",
                    ", ".join(unexpected_names),
                )
            return {
                name: (
                    "<redacted>"
                    if isinstance(name, str)
                    and name.casefold() not in valid_names
                    and name.upper() not in COMPOSE_ONLY_SETTINGS
                    else value
                )
                for name, value in values.items()
                if not (
                    isinstance(name, str)
                    and name.upper() in COMPOSE_ONLY_SETTINGS
                )
            }
        return values

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="forbid",
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
