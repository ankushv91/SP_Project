from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    PROJECT_NAME: str = "SP Project API"
    ENVIRONMENT: str = "development"
    DEBUG: bool = True

    DATABASE_URL: str

    AWS_SECRET_NAME: str = "sp-project/jwt-secret"
    AWS_REGION: str | None = None

    # Populated from AWS Secrets Manager during application startup.
    SECRET_KEY: str = ""
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
