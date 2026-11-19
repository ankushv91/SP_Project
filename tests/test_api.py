import importlib
import logging

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base, get_db
from app.domains.todos import router as todo_router

main_module = importlib.import_module("app.main")

app = main_module.app


@pytest.fixture
async def client(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "")
    monkeypatch.setattr(todo_router, "send_todo_created_email", lambda *args: None)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(main_module, "engine", engine)

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId):
            assert SecretId == settings.AWS_SECRET_NAME
            return {"SecretString": "runtime-test-secret-that-is-long-enough"}

    def fake_boto3_client(service_name, **kwargs):
        assert service_name == "secretsmanager"
        assert kwargs == (
            {"region_name": settings.AWS_REGION} if settings.AWS_REGION else {}
        )
        return FakeSecretsManager()

    monkeypatch.setattr(main_module.boto3, "client", fake_boto3_client)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def override_get_db():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with main_module.lifespan(app):
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
            ) as test_client:
                yield test_client
    finally:
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


@pytest.mark.asyncio
async def test_create_todo(client):
    response = await client.post(
        "/todos",
        json={"title": "Write tests", "description": "Cover CRUD operations"},
    )
    assert response.status_code == 201
    todo = response.json()
    assert todo["title"] == "Write tests"
    assert todo["description"] == "Cover CRUD operations"
    assert todo["is_completed"] is False


@pytest.mark.asyncio
async def test_create_todo_queues_email_after_saving(client, monkeypatch, caplog):
    queued_emails = []
    monkeypatch.setattr(
        todo_router,
        "send_todo_created_email",
        lambda *args: queued_emails.append(args),
    )
    caplog.set_level(logging.INFO, logger="app.domains.todos.router")

    response = await client.post("/todos", json={"title": "Notify me"})

    assert response.status_code == 201
    todo = response.json()
    assert queued_emails == [
        (todo["id"], "Notify me", settings.TODO_NOTIFICATION_EMAIL)
    ]
    assert (
        f"Queued todo creation email notification for todo_id={todo['id']}"
        in caplog.text
    )


@pytest.mark.asyncio
async def test_list_todos_filters_and_paginates(client):
    pending = await client.post(
        "/todos", json={"title": "Pending", "is_completed": False}
    )
    completed = await client.post(
        "/todos", json={"title": "Completed", "is_completed": True}
    )
    pending_id = pending.json()["id"]
    completed_id = completed.json()["id"]

    listed = await client.get("/todos")
    assert listed.status_code == 200
    assert {item["id"] for item in listed.json()} == {pending_id, completed_id}
    assert [
        item["id"]
        for item in (
            await client.get("/todos", params={"completed": True})
        ).json()
    ] == [completed_id]
    assert [
        item["id"]
        for item in (
            await client.get("/todos", params={"completed": False})
        ).json()
    ] == [pending_id]
    assert len((await client.get("/todos", params={"limit": 1})).json()) == 1
    assert len(
        (await client.get("/todos", params={"skip": 1, "limit": 1})).json()
    ) == 1


@pytest.mark.asyncio
async def test_get_todo(client):
    created = await client.post(
        "/todos",
        json={"title": "Read todo", "description": "Fetch by ID"},
    )
    todo_id = created.json()["id"]

    fetched = await client.get(f"/todos/{todo_id}")
    assert fetched.status_code == 200
    assert fetched.json()["description"] == "Fetch by ID"


@pytest.mark.asyncio
async def test_update_todo(client):
    created = await client.post(
        "/todos",
        json={"title": "Original title", "description": "Keep this"},
    )
    todo_id = created.json()["id"]

    updated = await client.put(
        f"/todos/{todo_id}",
        json={"title": "Updated title", "is_completed": True},
    )
    assert updated.status_code == 200
    assert updated.json()["title"] == "Updated title"
    assert updated.json()["is_completed"] is True
    assert updated.json()["description"] == "Keep this"


@pytest.mark.asyncio
async def test_delete_todo(client):
    created = await client.post("/todos", json={"title": "Delete todo"})
    todo_id = created.json()["id"]

    deleted = await client.delete(f"/todos/{todo_id}")
    assert deleted.status_code == 204
    assert (await client.get(f"/todos/{todo_id}")).status_code == 404


@pytest.mark.asyncio
async def test_todo_get_update_and_delete_return_404(client):
    assert (await client.get("/todos/999999")).status_code == 404
    assert (
        await client.put("/todos/999999", json={"title": "Missing"})
    ).status_code == 404
    assert (await client.delete("/todos/999999")).status_code == 404


@pytest.mark.asyncio
async def test_auth_registration_token_and_profile(client):
    registration = await client.post(
        "/auth/register",
        json={
            "username": "test-user",
            "email": "test@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert registration.status_code == 201
    assert "password" not in registration.json()

    duplicate_username = await client.post(
        "/auth/register",
        json={
            "username": "test-user",
            "email": "another@example.com",
            "password": "password",
        },
    )
    assert duplicate_username.status_code == 400

    duplicate_email = await client.post(
        "/auth/register",
        json={
            "username": "another-user",
            "email": "test@example.com",
            "password": "password",
        },
    )
    assert duplicate_email.status_code == 400

    token_response = await client.post(
        "/auth/token",
        data={
            "username": "test@example.com",
            "password": "correct horse battery staple",
        },
    )
    assert token_response.status_code == 200
    token = token_response.json()["access_token"]

    profile = await client.get(
        "/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert profile.status_code == 200
    assert profile.json()["email"] == "test@example.com"

    wrong_password = await client.post(
        "/auth/token",
        data={"username": "test@example.com", "password": "incorrect"},
    )
    assert wrong_password.status_code == 401

    unknown_user = await client.post(
        "/auth/token",
        data={"username": "unknown@example.com", "password": "password"},
    )
    assert unknown_user.status_code == 401

    assert (await client.get("/auth/me")).status_code == 401
    invalid_token = await client.get(
        "/auth/me", headers={"Authorization": "Bearer invalid"}
    )
    assert invalid_token.status_code == 401


@pytest.mark.asyncio
async def test_health_check(client):
    response = await client.get("/health-check")
    assert response.status_code == 200
    assert response.json()["status"] == "success"
    assert response.json()["environment"]


@pytest.mark.asyncio
async def test_health_check_reports_database_errors(client):
    async def failing_get_db():
        class FailingSession:
            async def execute(self, query):
                raise RuntimeError("database unavailable")

        yield FailingSession()

    original_override = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = failing_get_db
    try:
        response = await client.get("/health-check")
    finally:
        app.dependency_overrides[get_db] = original_override

    assert response.status_code == 200
    assert response.json() == {
        "status": "error",
        "message": "database unavailable",
    }


@pytest.mark.asyncio
async def test_lifespan_creates_tables(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(main_module, "engine", engine)

    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId):
            assert SecretId == settings.AWS_SECRET_NAME
            return {"SecretString": "runtime-test-secret-that-is-long-enough"}

    def fake_boto3_client(service_name, **kwargs):
        assert service_name == "secretsmanager"
        assert kwargs == (
            {"region_name": settings.AWS_REGION} if settings.AWS_REGION else {}
        )
        return FakeSecretsManager()

    monkeypatch.setattr(main_module.boto3, "client", fake_boto3_client)
    try:
        async with main_module.lifespan(app):
            assert settings.SECRET_KEY == "runtime-test-secret-that-is-long-enough"
            async with engine.connect() as connection:
                tables = await connection.run_sync(
                    lambda sync_connection: set(
                        inspect(sync_connection).get_table_names()
                    )
                )
            assert {"todos", "users"} <= tables
    finally:
        await engine.dispose()


def test_fetch_jwt_secret_rejects_missing_secret_string(monkeypatch):
    class FakeSecretsManager:
        def get_secret_value(self, *, SecretId):
            return {}

    monkeypatch.setattr(
        main_module.boto3,
        "client",
        lambda service_name, **kwargs: FakeSecretsManager(),
    )

    with pytest.raises(RuntimeError, match="non-empty SecretString"):
        main_module.fetch_jwt_secret()
