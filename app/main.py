from contextlib import asynccontextmanager
import json
from pathlib import Path
import boto3
from fastapi import APIRouter, Depends, FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.api_versioning import (
    AcceptHeaderVersionMiddleware,
    DEFAULT_API_VERSION,
    SUPPORTED_API_VERSIONS,
)
from app.core.config import settings
from app.core.database import get_db, engine, Base
from app.core.rate_limit import RedisRateLimitMiddleware
from app.core.security import set_jwt_secret
from app.domains.users.router import router as auth_router
from app.domains.todos.router import router as todo_router

OPENAPI_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "openapi.json"
TODO_CLIENT_PATH = Path(__file__).resolve().parent / "domains" / "todos" / "client.html"


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

    app.state.openapi_schemas = {
        version: get_version_openapi(version) for version in versioned_apis
    }
    OPENAPI_SCHEMA_PATH.write_text(
        json.dumps(
            app.state.openapi_schemas[DEFAULT_API_VERSION],
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    yield


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


def create_version_router(version: str) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/version",
        operation_id=f"get_{version}_version",
        tags=["API version"],
    )
    async def get_api_version() -> dict[str, str]:
        return {"version": version}

    return router


def create_versioned_api(version: str) -> FastAPI:
    versioned_app = FastAPI(
        title=f"{settings.PROJECT_NAME} API {version}",
        version=version,
        debug=settings.DEBUG,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    versioned_app.add_middleware(RedisRateLimitMiddleware)
    versioned_app.add_api_route(
        "/",
        lambda: FileResponse(TODO_CLIENT_PATH),
        methods=["GET"],
        include_in_schema=False,
    )
    versioned_app.include_router(auth_router)
    versioned_app.include_router(todo_router)
    versioned_app.include_router(create_version_router(version))
    versioned_app.add_api_route(
        "/health-check",
        health_check,
        methods=["GET"],
        responses={503: {"description": "Database connection failed"}},
        tags=["Health"],
    )
    return versioned_app


versioned_apis = {
    version: create_versioned_api(version) for version in SUPPORTED_API_VERSIONS
}


def get_version_openapi(version: str) -> dict:
    schema = versioned_apis[version].openapi()
    paths = dict(schema["paths"])
    paths.update(
        {
            f"/{version}{path}": operations
            for path, operations in schema["paths"].items()
        }
    )
    return {**schema, "paths": paths}


app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    debug=settings.DEBUG,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.add_middleware(
    AcceptHeaderVersionMiddleware,
    apps=versioned_apis,
    default_version=DEFAULT_API_VERSION,
    openapi_schemas={
        version: lambda version=version: get_version_openapi(version)
        for version in versioned_apis
    },
)
for versioned_app in versioned_apis.values():
    versioned_app.dependency_overrides = app.dependency_overrides
