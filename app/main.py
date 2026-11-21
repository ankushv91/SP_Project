from contextlib import asynccontextmanager
import json
from pathlib import Path
import boto3
from fastapi import Depends, FastAPI, HTTPException, status
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_db, engine, Base
from app.core.security import set_jwt_secret
from app.domains.users.router import router as auth_router
from app.domains.todos.router import router as todo_router

OPENAPI_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "openapi.json"


def fetch_jwt_secret() -> str:
    client_options = {}
    if settings.AWS_REGION:
        client_options["region_name"] = settings.AWS_REGION
    client = boto3.client("secretsmanager", **client_options)
    response = client.get_secret_value(SecretId=settings.AWS_SECRET_NAME)
    secret = response.get("SecretString")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError(
            "AWS Secrets Manager returned no non-empty SecretString for the configured JWT secret"
        )
    if len(secret.encode("utf-8")) < 32:
        raise RuntimeError("JWT secret must contain at least 32 bytes")
    return secret

@asynccontextmanager
async def lifespan(app: FastAPI):
    set_jwt_secret(await run_in_threadpool(fetch_jwt_secret))
    # Auto-create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    OPENAPI_SCHEMA_PATH.write_text(
        json.dumps(app.openapi(), indent=2) + "\n",
        encoding="utf-8",
    )
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    debug=settings.DEBUG,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

# Register domain routers
app.include_router(auth_router)
app.include_router(todo_router)

@app.get(
    "/health-check",
    responses={503: {"description": "Database connection failed"}},
)
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        await db.execute(text("SELECT 1"))
        return {
            "status": "success",
            "message": "Successfully connected to PostgreSQL!",
            "environment": settings.ENVIRONMENT
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(e),
        ) from e
