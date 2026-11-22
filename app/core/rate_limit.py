import hashlib
import logging
import math
import time
from dataclasses import dataclass
from uuid import uuid4

from redis.exceptions import RedisError
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.redis import get_redis_client

logger = logging.getLogger(__name__)

SLIDING_WINDOW_SCRIPT = """
local now = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now - window)
local count = redis.call('ZCARD', KEYS[1])
if count >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_after = window - (now - tonumber(oldest[2]))
    return {0, math.max(1, math.ceil(retry_after / 1000)), count}
end
redis.call('ZADD', KEYS[1], now, ARGV[4])
redis.call('PEXPIRE', KEYS[1], window)
return {1, 0, count + 1}
"""


@dataclass(frozen=True)
class RateLimit:
    limit: int
    window_seconds: int


RATE_LIMITS: dict[tuple[str, str], RateLimit] = {
    ("GET", "/health-check"): RateLimit(limit=20, window_seconds=60),
    ("POST", "/auth/register"): RateLimit(limit=5, window_seconds=60),
    ("POST", "/auth/token"): RateLimit(limit=10, window_seconds=60),
    ("GET", "/auth/me"): RateLimit(limit=60, window_seconds=60),
    ("GET", "/todos"): RateLimit(limit=60, window_seconds=60),
    ("POST", "/todos"): RateLimit(limit=30, window_seconds=60),
    ("GET", "/todos/{todo_id}"): RateLimit(limit=60, window_seconds=60),
    ("PUT", "/todos/{todo_id}"): RateLimit(limit=30, window_seconds=60),
    ("DELETE", "/todos/{todo_id}"): RateLimit(limit=30, window_seconds=60),
}


def _policy_for(method: str, path: str) -> tuple[str, RateLimit] | None:
    exact_key = (method, path)
    if exact_key in RATE_LIMITS:
        return path, RATE_LIMITS[exact_key]

    todo_id_prefix = "/todos/"
    if path.startswith(todo_id_prefix) and path[len(todo_id_prefix) :].isdigit():
        route_path = "/todos/{todo_id}"
        policy = RATE_LIMITS.get((method, route_path))
        if policy:
            return route_path, policy
    return None


class RedisRateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        matched_policy = _policy_for(request.method, request.url.path)
        if matched_policy is None:
            return await call_next(request)

        route_path, policy = matched_policy
        client_host = request.client.host if request.client else "unknown"
        client_hash = hashlib.sha256(client_host.encode("utf-8")).hexdigest()
        key = f"rate-limit:{request.method}:{route_path}:{client_hash}"
        now_ms = time.time_ns() // 1_000_000
        window_ms = policy.window_seconds * 1_000

        try:
            allowed, retry_after, used = await get_redis_client().eval(
                SLIDING_WINDOW_SCRIPT,
                1,
                key,
                now_ms,
                window_ms,
                policy.limit,
                uuid4().hex,
            )
        except RedisError:
            logger.exception(
                "Redis rate limiter unavailable for %s %s",
                request.method,
                route_path,
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "Rate limiter unavailable"},
            )

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={
                    "Retry-After": str(retry_after),
                    "X-RateLimit-Limit": str(policy.limit),
                    "X-RateLimit-Remaining": "0",
                },
            )

        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(policy.limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, policy.limit - used))
        return response
