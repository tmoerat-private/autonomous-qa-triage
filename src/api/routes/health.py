import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from src.config.settings import get_settings
from src.db.session import get_engine

router = APIRouter(tags=["health"])


@router.get("/api/v1/integrations/status")
async def integrations_status() -> dict:
    """Report which integrations have credentials configured in the environment.

    Checks for non-empty credential values only — does not make live network
    calls to each provider.  This is intentionally fast and safe to poll.
    """
    settings = get_settings()

    def _connected(*keys: str) -> bool:
        return all(bool(getattr(settings, k, "")) for k in keys)

    items: dict[str, bool] = {
        "github_actions": _connected("github_webhook_secret", "github_app_id"),
        "jenkins": _connected("jenkins_webhook_secret", "jenkins_url"),
        "jira": _connected("jira_url", "jira_api_token"),
        "slack": _connected("slack_bot_token"),
    }

    return {
        "integrations": {
            key: {
                "status": "ok" if ok else "not_configured",
                "connected": ok,
                "detail": "Credentials configured" if ok else "No credentials found in environment",
            }
            for key, ok in items.items()
        }
    }


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
