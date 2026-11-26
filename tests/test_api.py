import importlib
import json
import logging
import math
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import rate_limit
from app.core.api_versioning import (
    AcceptHeaderVersionMiddleware,
    NotAcceptableAPIRequest,
    parse_accept_version,
)
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.core.security import _get_secret_key, set_jwt_secret
from app.domains.todos import router as todo_router
from app.domains.todos.websocket import todo_connection_manager
from starlette.websockets import WebSocketDisconnect

main_module = importlib.import_module("app.main")

app = main_module.app


class InMemoryRedis:
    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.sorted_sets = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, seconds, value):
        self.values[key] = value
        self.expirations[key] = seconds

    def scan_iter(self, match):
        prefix = match.removesuffix("*")

        async def matching_keys():
            for key in list(self.values):
                if key.startswith(prefix):
                    yield key

        return matching_keys()

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            if key in self.values:
                del self.values[key]
                self.expirations.pop(key, None)
                deleted += 1
        return deleted

    async def eval(self, script, number_of_keys, *args):
        key, now_ms, window_ms, limit, member = args
        now_ms = int(now_ms)
        window_ms = int(window_ms)
        limit = int(limit)
        entries = self.sorted_sets.setdefault(key, {})
        entries = {
            entry: score
            for entry, score in entries.items()
            if score > now_ms - window_ms
        }
        self.sorted_sets[key] = entries

        if len(entries) >= limit:
            oldest_score = min(entries.values())
            retry_after = max(
                1, math.ceil((window_ms - (now_ms - oldest_score)) / 1_000)
            )
            return [0, retry_after, len(entries)]

        entries[member] = now_ms
        return [1, 0, len(entries)]


@pytest.fixture
async def client(monkeypatch, tmp_path):
    set_jwt_secret("")
    monkeypatch.setattr(todo_router, "send_todo_created_email", AsyncMock())
    monkeypatch.setattr(
        main_module, "OPENAPI_SCHEMA_PATH", tmp_path / "openapi.json"
    )
    redis_cache = InMemoryRedis()
    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: redis_cache)
    monkeypatch.setattr(
        todo_router.todo_service, "get_redis_client", lambda: redis_cache
    )
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
async def test_create_todo_broadcasts_saved_todo(client, monkeypatch):
    broadcast = AsyncMock()
    monkeypatch.setattr(todo_connection_manager, "broadcast", broadcast)

    response = await client.post(
        "/todos",
        json={"title": "Broadcast this", "description": "To connected clients"},
    )

    assert response.status_code == 201
    broadcast.assert_awaited_once_with(
        {"event": "todo_created", "todo": response.json()}
    )


@pytest.mark.asyncio
async def test_todo_websocket_removes_disconnected_client():
    class DisconnectedWebSocket:
        accepted = False

        async def accept(self):
            self.accepted = True

        async def receive_text(self):
            raise WebSocketDisconnect()

    websocket = DisconnectedWebSocket()
    await todo_router.todos_websocket(websocket)

    assert websocket.accepted
    assert websocket not in todo_connection_manager.active_connections


@pytest.mark.asyncio
async def test_todo_websocket_broadcasts_to_all_and_drops_closed_connections(
    monkeypatch,
):
    class ConnectedWebSocket:
        def __init__(self):
            self.messages = []

        async def send_json(self, payload):
            self.messages.append(payload)

    class ClosedWebSocket:
        async def send_json(self, payload):
            raise RuntimeError("WebSocket is already closed")

    first = ConnectedWebSocket()
    second = ConnectedWebSocket()
    closed = ClosedWebSocket()
    monkeypatch.setattr(
        todo_connection_manager,
        "active_connections",
        {first, second, closed},
    )
    payload = {"event": "todo_created", "todo": {"id": 1}}

    await todo_connection_manager.broadcast(payload)

    assert first.messages == [payload]
    assert second.messages == [payload]
    assert todo_connection_manager.active_connections == {first, second}


@pytest.mark.asyncio
async def test_websocket_requests_dispatch_to_selected_version():
    dispatched = {}
    sent_messages = []

    async def versioned_app(scope, receive, send):
        dispatched["scope"] = scope
        await send({"type": "websocket.accept"})

    middleware = AcceptHeaderVersionMiddleware(
        versioned_app,
        apps={"v1": versioned_app},
        default_version="v1",
    )

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent_messages.append(message)

    await middleware(
        {
            "type": "websocket",
            "path": "/v1/todos/ws",
            "raw_path": b"/v1/todos/ws",
            "headers": [(b"accept", b"text/html")],
            "root_path": "",
            "state": {},
        },
        receive,
        send,
    )

    assert dispatched["scope"]["path"] == "/todos/ws"
    assert dispatched["scope"]["state"]["api_version"] == "v1"
    assert sent_messages == [{"type": "websocket.accept"}]


@pytest.mark.asyncio
async def test_todo_client_page_is_available(client):
    response = await client.get("/")

    assert response.status_code == 200
    assert "Live Todo updates" in response.text


@pytest.mark.asyncio
async def test_create_todo_queues_email_after_saving(client, monkeypatch, caplog):
    send_notification = AsyncMock()
    monkeypatch.setattr(todo_router, "send_todo_created_email", send_notification)
    caplog.set_level(logging.INFO, logger="app.domains.todos.router")

    response = await client.post("/todos", json={"title": "Notify me"})

    assert response.status_code == 201
    todo = response.json()
    send_notification.assert_awaited_once_with(
        todo["id"], "Notify me", settings.TODO_NOTIFICATION_EMAIL
    )
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
async def test_todo_list_cache_reuses_results_and_invalidates_after_mutations(
    client, monkeypatch
):
    cache = InMemoryRedis()
    monkeypatch.setattr(todo_router.todo_service, "get_redis_client", lambda: cache)
    original_execute = AsyncSession.execute
    execute_count = 0

    async def counted_execute(self, *args, **kwargs):
        nonlocal execute_count
        execute_count += 1
        return await original_execute(self, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "execute", counted_execute)

    assert (await client.get("/todos")).json() == []
    assert cache.values
    assert (await client.get("/todos")).json() == []
    assert execute_count == 1
    assert list(cache.expirations.values()) == [60]

    created = await client.post("/todos", json={"title": "Cached todo"})
    todo_id = created.json()["id"]
    assert (await client.get("/todos")).json()[0]["id"] == todo_id
    assert execute_count == 2

    updated = await client.put(
        f"/todos/{todo_id}", json={"is_completed": True}
    )
    assert updated.status_code == 200
    assert execute_count == 3
    completed = await client.get("/todos", params={"completed": True})
    assert [todo["id"] for todo in completed.json()] == [todo_id]
    assert execute_count == 4
    assert (await client.get("/todos", params={"completed": True})).json() == (
        completed.json()
    )
    assert execute_count == 4

    deleted = await client.delete(f"/todos/{todo_id}")
    assert deleted.status_code == 204
    assert execute_count == 5
    assert (await client.get("/todos")).json() == []
    assert execute_count == 6


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
async def test_accept_header_routes_to_requested_api_version(client):
    for version in ("v1", "v2"):
        response = await client.get(
            "/version",
            headers={
                "Accept": f"application/vnd.sp-project.{version}+json"
            },
        )
        assert response.status_code == 200
        assert response.json() == {"version": version}
        assert response.headers["X-API-Version"] == version
        assert response.headers["Vary"] == "Accept"


@pytest.mark.asyncio
async def test_url_prefix_routes_to_requested_version(client):
    response = await client.get(
        "/v2/version",
        headers={"Accept": "application/vnd.sp-project.v1+json"},
    )

    assert response.status_code == 200
    assert response.json() == {"version": "v2"}
    assert response.headers["X-API-Version"] == "v2"


@pytest.mark.asyncio
async def test_url_prefix_routes_existing_api_endpoints(client):
    response = await client.get("/v2/todos")

    assert response.status_code == 200
    assert response.json() == []
    assert response.headers["X-API-Version"] == "v2"


@pytest.mark.asyncio
async def test_accept_header_version_parameter_and_default_version(client):
    version_two = await client.get(
        "/version",
        headers={"Accept": "application/json; version=2"},
    )
    default_version = await client.get("/version")

    assert version_two.json() == {"version": "v2"}
    assert default_version.json() == {"version": "v1"}


@pytest.mark.asyncio
async def test_accept_header_uses_quality_to_choose_between_versions(client):
    response = await client.get(
        "/version",
        headers={
            "Accept": (
                "application/vnd.sp-project.v1+json;q=0.2, "
                "application/vnd.sp-project.v2+json;q=0.9"
            )
        },
    )

    assert response.status_code == 200
    assert response.json() == {"version": "v2"}


@pytest.mark.asyncio
async def test_accept_header_rejects_unsupported_versions_and_media_types(client):
    unsupported_version = await client.get(
        "/version",
        headers={"Accept": "application/vnd.sp-project.v9+json"},
    )
    unsupported_media_type = await client.get(
        "/version",
        headers={"Accept": "text/plain"},
    )

    assert unsupported_version.status_code == 406
    assert unsupported_version.json()["supported_versions"] == ["v1", "v2"]
    assert unsupported_media_type.status_code == 406


def test_accept_version_normalization_does_not_double_prefix():
    assert parse_accept_version(
        "application/vnd.sp-project.1+json"
    ) == "v1"
    assert parse_accept_version(
        "application/vnd.sp-project.v1+json"
    ) == "v1"
    assert parse_accept_version(
        "application/json; version=V2"
    ) == "v2"
    with pytest.raises(NotAcceptableAPIRequest, match="Unsupported API version"):
        parse_accept_version("application/json; version=vv1")


@pytest.mark.asyncio
async def test_versioned_openapi_documents_are_available(client):
    for version in ("v1", "v2"):
        response = await client.get(f"/openapi/{version}.json")

        assert response.status_code == 200
        assert response.json()["info"]["version"] == version
        assert "/version" in response.json()["paths"]
        assert f"/{version}/todos" in response.json()["paths"]
        assert response.headers["X-API-Version"] == version


@pytest.mark.asyncio
async def test_rate_limit_returns_429_after_endpoint_limit(client, monkeypatch):
    monkeypatch.setitem(
        rate_limit.RATE_LIMITS,
        ("GET", "/health-check"),
        rate_limit.RateLimit(limit=2, window_seconds=60),
    )

    first = await client.get("/health-check")
    assert first.status_code == 200
    assert first.headers["X-RateLimit-Limit"] == "2"
    assert first.headers["X-RateLimit-Remaining"] == "1"

    second = await client.get("/health-check")
    assert second.status_code == 200
    assert second.headers["X-RateLimit-Remaining"] == "0"

    limited = await client.get("/health-check")

    assert limited.status_code == 429
    assert limited.json() == {"detail": "Rate limit exceeded"}
    assert limited.headers["Retry-After"].isdigit()
    assert limited.headers["X-RateLimit-Remaining"] == "0"


def test_todo_rate_limit_matches_non_numeric_single_segment_ids():
    expected = rate_limit.RATE_LIMITS[("GET", "/todos/{todo_id}")]

    assert rate_limit._policy_for("GET", "/todos/abc-123") == (
        "/todos/{todo_id}",
        expected,
    )
    assert rate_limit._policy_for("GET", "/todos/abc-123/nested") is None


@pytest.mark.asyncio
async def test_rate_limited_endpoint_fails_closed_when_redis_is_unavailable(
    client, monkeypatch
):
    class UnavailableRedis:
        async def eval(self, *args):
            raise RedisConnectionError("Redis is unavailable")

    monkeypatch.setattr(rate_limit, "get_redis_client", lambda: UnavailableRedis())

    response = await client.get("/health-check")

    assert response.status_code == 503
    assert response.json() == {"detail": "Rate limiter unavailable"}


@pytest.mark.asyncio
async def test_builtin_api_documentation_routes_are_disabled(client):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 404


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

    assert response.status_code == 503
    assert response.json() == {"detail": "database unavailable"}


@pytest.mark.asyncio
async def test_lifespan_creates_tables_and_generates_openapi(monkeypatch, tmp_path):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    monkeypatch.setattr(main_module, "engine", engine)
    openapi_path = tmp_path / "openapi.json"
    monkeypatch.setattr(main_module, "OPENAPI_SCHEMA_PATH", openapi_path)

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
            assert _get_secret_key() == "runtime-test-secret-that-is-long-enough"
            schema = json.loads(openapi_path.read_text(encoding="utf-8"))
            assert "/todos" in schema["paths"]
            assert schema["components"]["schemas"]["TodoCreate"]["examples"][0][
                "title"
            ] == "Buy groceries"
            assert schema["components"]["schemas"]["UserCreate"]["examples"][0][
                "username"
            ] == "jane.doe"
            assert "503" in schema["paths"]["/health-check"]["get"]["responses"]
            for version in main_module.versioned_apis:
                version_schema = main_module.get_version_openapi(version)
                assert version_schema["info"]["version"] == version
                assert "/version" in version_schema["paths"]
                assert f"/{version}/todos" in version_schema["paths"]
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


def test_settings_reject_unknown_dotenv_keys_without_logging_values(
    tmp_path, caplog
):
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "DATABASE_URL=sqlite+aiosqlite:///:memory:\n"
        "UNEXPECTED_OPTION=do-not-log-this-value\n",
        encoding="utf-8",
    )
    caplog.set_level(logging.WARNING, logger="app.core.config")

    with pytest.raises(ValidationError, match="unexpected_option") as error:
        Settings(_env_file=env_file)

    assert "UNEXPECTED_OPTION" in caplog.text
    assert "do-not-log-this-value" not in caplog.text
    assert "do-not-log-this-value" not in str(error.value)


def test_settings_allow_known_compose_only_dotenv_keys(tmp_path):
    env_file = tmp_path / "compose.env"
    env_file.write_text(
        "DATABASE_URL=sqlite+aiosqlite:///:memory:\n"
        "POSTGRES_PORT=5433\n"
        "AWS_PROFILE=development\n"
        "KIBANA_ENCRYPTION_KEY=local-development-key\n",
        encoding="utf-8",
    )

    configured = Settings(_env_file=env_file)

    assert configured.DATABASE_URL == "sqlite+aiosqlite:///:memory:"


def test_settings_use_sqlite_when_database_url_is_not_configured(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)

    configured = Settings(_env_file=None)

    assert configured.DATABASE_URL == "sqlite+aiosqlite:///./test.db"
