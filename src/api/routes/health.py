import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config.settings import get_settings
from src.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Liveness probe — always returns 200 if the process is running."""
    return {"status": "ok", "version": "0.1.0", "service": "autonomous-qa"}


@router.get("/readiness")
async def readiness():
    """Readiness probe — checks PostgreSQL and Redis connectivity.

    Returns HTTP 200 when all dependencies are reachable, HTTP 503 otherwise.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # Check PostgreSQL
    try:
        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks["postgres"] = "ok"
    except Exception as exc:
        checks["postgres"] = f"error: {exc}"
        all_ok = False

    # Check Redis
    try:
        settings = get_settings()
        redis_client = aioredis.from_url(settings.redis_url)
        await redis_client.ping()
        await redis_client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        all_ok = False

    if all_ok:
        return {"status": "ready", "checks": checks}

    return JSONResponse(
        content={"status": "not_ready", "checks": checks},
        status_code=503,
    )
