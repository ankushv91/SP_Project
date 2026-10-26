from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.config import settings
from app.core.database import get_db, engine, Base
from app.domains.users.router import router as auth_router
from app.domains.todos.router import router as todo_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Auto-create tables on startup
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    lifespan=lifespan,
    debug=settings.DEBUG
)

# Register domain routers
app.include_router(auth_router)
app.include_router(todo_router)

@app.get("/health-check")
async def health_check(db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(text("SELECT 1"))
        return {
            "status": "success",
            "message": "Successfully connected to PostgreSQL!",
            "environment": settings.ENVIRONMENT
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
