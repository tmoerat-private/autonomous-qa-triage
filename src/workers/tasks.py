import asyncio

import structlog

from src.observability.metrics import TRIAGE_COMPLETED
from src.services.triage_service import run_triage
from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(bind=True, max_retries=3, default_retry_delay=60)
def run_triage_pipeline(self, pipeline_event_id: str) -> dict:
    """Entry point called after a webhook is received.

    Runs the full triage pipeline for a given pipeline event.
    Pipeline Monitor + agent orchestration will be wired in Sprint 2/3.
    """
    log = logger.bind(pipeline_event_id=pipeline_event_id, task_id=self.request.id)
    log.info("triage_pipeline.started")
    try:
        result = asyncio.run(run_triage(pipeline_event_id))
        log.info("triage_pipeline.completed", result=result)
        TRIAGE_COMPLETED.labels(status="success").inc()
        return result
    except Exception as exc:
        log.warning("triage_pipeline.failed", error=str(exc))
        TRIAGE_COMPLETED.labels(status="failed").inc()
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
