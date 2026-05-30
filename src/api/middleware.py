import time
import uuid
from typing import Callable

import structlog
from redis.asyncio import Redis
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from src.config.settings import get_settings

logger = structlog.get_logger()

_RATE_LIMIT_REQUESTS: int = 100
_RATE_LIMIT_WINDOW_SECONDS: int = 60


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Reads X-Request-ID from the incoming request (or generates a UUID4) and
    propagates it to both request.state and the outgoing response headers."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class LoggingMiddleware(BaseHTTPMiddleware):
    """Logs method, path, status_code, duration_ms, and request_id for every
    request using structlog structured JSON logging."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = int((time.monotonic() - start) * 1000)
        request_id = getattr(request.state, "request_id", "unknown")
        logger.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            request_id=request_id,
        )
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP sliding-window rate limiter for webhook endpoints.

    Applies only to paths under /api/v1/webhooks.
    Allows up to 100 requests per 60-second window per client IP, tracked in
    Redis as a sorted set.  Fails open when Redis is unavailable so that a
    Redis outage never blocks legitimate webhook traffic.

    Key pattern: rate_limit:{ip}:{window_minute}
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        settings = get_settings()
        self._redis_url: str = settings.redis_url

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not request.url.path.startswith("/api/v1/webhooks"):
            return await call_next(request)

        client_ip: str = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )

        now_ms = int(time.time() * 1000)
        window_minute = int(time.time()) // _RATE_LIMIT_WINDOW_SECONDS
        redis_key = f"rate_limit:{client_ip}:{window_minute}"
        window_start_ms = window_minute * _RATE_LIMIT_WINDOW_SECONDS * 1000

        try:
            async with Redis.from_url(self._redis_url, decode_responses=False) as redis:
                pipe = redis.pipeline()
                # Remove entries older than the current window
                pipe.zremrangebyscore(redis_key, "-inf", window_start_ms - 1)
                # Add this request (score = timestamp ms, member = unique id)
                pipe.zadd(redis_key, {f"{now_ms}-{uuid.uuid4()}": now_ms})
                # Count requests in the current window
                pipe.zcard(redis_key)
                # Expire the key slightly beyond one window for automatic cleanup
                pipe.expire(redis_key, _RATE_LIMIT_WINDOW_SECONDS * 2)
                results = await pipe.execute()

            request_count: int = results[2]
            if request_count > _RATE_LIMIT_REQUESTS:
                logger.warning(
                    "rate_limit.exceeded",
                    client_ip=client_ip,
                    path=request.url.path,
                    request_count=request_count,
                    limit=_RATE_LIMIT_REQUESTS,
                )
                return JSONResponse(
                    status_code=429,
                    content={"detail": "rate limit exceeded"},
                    headers={"Retry-After": str(_RATE_LIMIT_WINDOW_SECONDS)},
                )

        except Exception as exc:
            logger.warning(
                "rate_limit.redis_unavailable",
                error=str(exc),
                client_ip=client_ip,
                path=request.url.path,
            )
            # Fail open — don't block traffic when Redis is down

        return await call_next(request)
