from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    PROJECT_NAME: str = "SP Project API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:9102@localhost:5432/sp_project"

    # JWT Security
    SECRET_KEY: str = "your-super-secret-key-change-in-production-sp-project"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()

settings = get_settings()
