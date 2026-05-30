from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(app_client):
    """Liveness probe always returns 200 with expected payload."""
    response = await app_client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.1.0"
    assert data["service"] == "autonomous-qa"


@pytest.mark.asyncio
async def test_readiness_when_both_services_up(app_client):
    """Readiness probe returns 200 when PostgreSQL and Redis are reachable."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with (
        patch("src.api.routes.health.get_engine", return_value=mock_engine),
        patch("src.api.routes.health.aioredis.from_url", return_value=mock_redis),
    ):
        response = await app_client.get("/readiness")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert data["checks"]["postgres"] == "ok"
    assert data["checks"]["redis"] == "ok"


@pytest.mark.asyncio
async def test_readiness_returns_503_when_db_down(app_client):
    """Readiness probe returns 503 when PostgreSQL connection fails."""
    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(
        side_effect=Exception("Connection refused")
    )
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.aclose = AsyncMock()

    with (
        patch("src.api.routes.health.get_engine", return_value=mock_engine),
        patch("src.api.routes.health.aioredis.from_url", return_value=mock_redis),
    ):
        response = await app_client.get("/readiness")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert "error" in data["checks"]["postgres"]


@pytest.mark.asyncio
async def test_readiness_returns_503_when_redis_down(app_client):
    """Readiness probe returns 503 when Redis connection fails."""
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    mock_engine = MagicMock()
    mock_engine.connect.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_engine.connect.return_value.__aexit__ = AsyncMock(return_value=False)

    mock_redis = AsyncMock()
    mock_redis.ping = AsyncMock(side_effect=Exception("Redis unreachable"))
    mock_redis.aclose = AsyncMock()

    with (
        patch("src.api.routes.health.get_engine", return_value=mock_engine),
        patch("src.api.routes.health.aioredis.from_url", return_value=mock_redis),
    ):
        response = await app_client.get("/readiness")

    assert response.status_code == 503
    data = response.json()
    assert data["status"] == "not_ready"
    assert "error" in data["checks"]["redis"]
