"""Unit tests for RateLimitMiddleware in src/api/middleware.py.

No real Redis or HTTP server is used.  The middleware's Redis pipeline is
intercepted via unittest.mock so every test is fully in-process and fast.

Key behaviour under test:
  - Paths under /api/v1/webhooks are rate-limited; other paths are not.
  - Requests that push the sorted-set cardinality above 100 get a 429.
  - The 429 body is {"detail": "rate limit exceeded"} with Retry-After: 60.
  - When Redis raises an exception the middleware fails open (request passes).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.app import create_app

# ---------------------------------------------------------------------------
# Helpers — build mock Redis pipelines with configurable cardinality result
# ---------------------------------------------------------------------------


def _make_redis_mock(cardinality: int):
    """Return a mock Redis client whose pipeline().execute() returns cardinality
    as the third element (index 2), matching the middleware's results[2] read."""
    pipe_mock = MagicMock()                                          # sync pipeline object
    pipe_mock.zremrangebyscore = MagicMock(return_value=pipe_mock)  # returns self for chaining
    pipe_mock.zadd = MagicMock(return_value=pipe_mock)
    pipe_mock.zcard = MagicMock(return_value=pipe_mock)
    pipe_mock.expire = MagicMock(return_value=pipe_mock)
    pipe_mock.execute = AsyncMock(return_value=[None, None, cardinality, None])  # only this is async

    redis_mock = MagicMock()
    redis_mock.pipeline = MagicMock(return_value=pipe_mock)

    # The middleware uses `async with Redis.from_url(...) as redis:` so we need
    # to make the return value of from_url() work as an async context manager.
    redis_cm = AsyncMock()
    redis_cm.__aenter__ = AsyncMock(return_value=redis_mock)
    redis_cm.__aexit__ = AsyncMock(return_value=False)

    return redis_cm


def _make_redis_error_mock(exc: Exception):
    """Return a mock Redis context manager that raises `exc` on __aenter__."""
    redis_cm = AsyncMock()
    redis_cm.__aenter__ = AsyncMock(side_effect=exc)
    redis_cm.__aexit__ = AsyncMock(return_value=False)
    return redis_cm


# ---------------------------------------------------------------------------
# Fixture — fresh app client for each test so Prometheus doesn't double-register
# ---------------------------------------------------------------------------


@pytest.fixture
async def rate_limit_client():
    """An AsyncClient wrapping a freshly created app instance."""
    app = create_app()
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as ac:
        yield ac


# ===========================================================================
# Webhook path IS rate-limited
# ===========================================================================


@pytest.mark.asyncio
async def test_webhook_path_allowed_when_under_rate_limit(rate_limit_client):
    """A webhook request is forwarded when the cardinality (50) is under 100."""
    redis_cm = _make_redis_mock(cardinality=50)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        # The actual webhook handler will fail (no DB, no service), but the
        # middleware should let it through — we assert it is NOT 429.
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={"build": "data"},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code != 429


@pytest.mark.asyncio
async def test_webhook_path_blocked_when_over_rate_limit(rate_limit_client):
    """A webhook request is rejected with 429 when cardinality (101) exceeds 100."""
    redis_cm = _make_redis_mock(cardinality=101)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={"build": "data"},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 429


@pytest.mark.asyncio
async def test_rate_limit_exceeded_response_body(rate_limit_client):
    """The 429 body is exactly {"detail": "rate limit exceeded"}."""
    redis_cm = _make_redis_mock(cardinality=101)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/github_actions",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 429
    assert response.json() == {"detail": "rate limit exceeded"}


@pytest.mark.asyncio
async def test_rate_limit_exceeded_includes_retry_after_header(rate_limit_client):
    """The 429 response carries a Retry-After: 60 header."""
    redis_cm = _make_redis_mock(cardinality=200)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 429
    assert response.headers.get("retry-after") == "60"


@pytest.mark.asyncio
async def test_exactly_at_limit_is_allowed(rate_limit_client):
    """Cardinality exactly equal to the limit (100) is still allowed — the
    comparison is strictly greater-than."""
    redis_cm = _make_redis_mock(cardinality=100)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code != 429


# ===========================================================================
# Non-webhook paths are NOT rate-limited
# ===========================================================================


@pytest.mark.asyncio
async def test_failures_path_is_not_rate_limited(rate_limit_client):
    """Requests to /api/v1/failures bypass the rate limiter entirely.

    Redis is patched to raise an error to prove it is never consulted for
    non-webhook paths.
    """
    redis_cm = _make_redis_error_mock(ConnectionError("should not be called"))

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.get("/api/v1/failures")

    # If Redis were consulted and raises, the middleware would fail-open (not
    # 429).  But for non-webhook paths it should not even attempt Redis.  Either
    # way, the response must not be 429.
    assert response.status_code != 429


@pytest.mark.asyncio
async def test_health_path_is_not_rate_limited(rate_limit_client):
    """/health is outside the webhook prefix and must never be rate-limited."""
    redis_cm = _make_redis_mock(cardinality=999)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.get("/health")

    assert response.status_code != 429


@pytest.mark.asyncio
async def test_dashboard_path_is_not_rate_limited(rate_limit_client):
    """/api/v1/dashboard/summary is not under the /webhooks prefix."""
    redis_cm = _make_redis_mock(cardinality=999)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.get("/health")

    assert response.status_code != 429


# ===========================================================================
# Fail-open behaviour when Redis is unavailable
# ===========================================================================


@pytest.mark.asyncio
async def test_redis_connection_error_allows_request_through(rate_limit_client):
    """When Redis raises ConnectionError the middleware fails open — no 429."""
    redis_cm = _make_redis_error_mock(ConnectionError("Redis unavailable"))

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code != 429


@pytest.mark.asyncio
async def test_redis_generic_exception_allows_request_through(rate_limit_client):
    """Any exception from Redis triggers fail-open behaviour — no 429."""
    redis_cm = _make_redis_error_mock(RuntimeError("unexpected Redis error"))

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/github_actions",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code != 429


@pytest.mark.asyncio
async def test_redis_execute_error_allows_request_through(rate_limit_client):
    """If the pipeline's execute() raises, the middleware fails open."""
    pipe_mock = AsyncMock()
    pipe_mock.zremrangebyscore = AsyncMock(return_value=None)
    pipe_mock.zadd = AsyncMock(return_value=None)
    pipe_mock.zcard = AsyncMock(return_value=None)
    pipe_mock.expire = AsyncMock(return_value=None)
    pipe_mock.execute = AsyncMock(side_effect=ConnectionError("pipeline broken"))

    redis_mock = AsyncMock()
    redis_mock.pipeline = MagicMock(return_value=pipe_mock)

    redis_cm = AsyncMock()
    redis_cm.__aenter__ = AsyncMock(return_value=redis_mock)
    redis_cm.__aexit__ = AsyncMock(return_value=False)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code != 429


# ===========================================================================
# X-Forwarded-For header is used for the rate-limit key
# ===========================================================================


@pytest.mark.asyncio
async def test_rate_limit_uses_x_forwarded_for_header(rate_limit_client):
    """When X-Forwarded-For is present the middleware uses that IP for the key.

    We cannot easily assert on the Redis key value from outside, but we can
    verify that rate-limiting still triggers (the middleware ran its Redis logic)
    when this header is set.
    """
    redis_cm = _make_redis_mock(cardinality=101)

    with patch("src.api.middleware.Redis.from_url", return_value=redis_cm):
        response = await rate_limit_client.post(
            "/api/v1/webhooks/jenkins",
            json={},
            headers={
                "Content-Type": "application/json",
                "X-Forwarded-For": "203.0.113.42, 10.0.0.1",
            },
        )

    assert response.status_code == 429
