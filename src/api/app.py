import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.api.middleware import LoggingMiddleware, RateLimitMiddleware, RequestIdMiddleware
from src.api.routes.agents import router as agent_runs_router
from src.api.routes.dashboard import router as dashboard_router
from src.api.routes.failures import router as failures_router
from src.api.routes.failures import screenshots_router
from src.api.routes.health import router as health_router
from src.api.routes.releases import router as releases_router
from src.api.routes.webhooks import router as webhook_router
from src.config.logging_config import configure_logging
from src.config.settings import get_settings
from src.observability.metrics import mount_metrics_endpoint

logger = structlog.get_logger()


def create_app() -> FastAPI:
    """FastAPI application factory.

    Configures logging, middleware, and routes.  Intended to be called once at
    module import time (e.g. ``app = create_app()`` in main.py or gunicorn's
    ``--app`` argument).
    """
    settings = get_settings()
    configure_logging(settings.log_level, settings.app_env)

    app = FastAPI(
        title="Autonomous QA Failure Triage",
        version="0.1.0",
        description="AI-powered CI/CD failure triage agent platform",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    # Middleware — added in reverse order so the last-added is the outermost
    # wrapper and therefore the first to run on each incoming request.
    #
    # Execution order (request in → response out):
    #   RateLimitMiddleware → CORSMiddleware → RequestIdMiddleware → LoggingMiddleware →
    #   route handler
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(RateLimitMiddleware)

    # Routers
    app.include_router(health_router)
    app.include_router(webhook_router, prefix="/api/v1")
    app.include_router(failures_router, prefix="/api/v1")
    app.include_router(screenshots_router, prefix="/api/v1")
    app.include_router(dashboard_router, prefix="/api/v1")
    app.include_router(agent_runs_router, prefix="/api/v1")
    app.include_router(releases_router, prefix="/api/v1")

    # Prometheus metrics endpoint
    mount_metrics_endpoint(app)

    @app.exception_handler(Exception)
    async def upstream_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled_exception", error=str(exc), path=request.url.path)
        return JSONResponse(
            status_code=503,
            content={"detail": "upstream service unavailable"},
        )

    @app.on_event("startup")
    async def startup() -> None:
        logger.info(
            "autonomous_qa_starting",
            env=settings.app_env,
            port=settings.app_port,
        )

    @app.on_event("shutdown")
    async def shutdown() -> None:
        logger.info("autonomous_qa_stopping")

    return app
