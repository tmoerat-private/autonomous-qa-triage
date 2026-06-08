import asyncio
from uuid import UUID

import structlog
from celery import Task

from src.observability.metrics import TRIAGE_COMPLETED
from src.services.triage_service import run_triage
from src.workers.celery_app import celery_app

logger = structlog.get_logger(__name__)


async def _mark_event_failed(pipeline_event_id: str) -> None:
    """Open a fresh async DB session and mark the pipeline event as failed.

    This helper is intentionally separate so on_failure can drive it with
    asyncio.run() without touching FastAPI's request-scoped DbSession.
    """
    from src.db.repositories.pipeline_repo import PipelineEventRepository
    from src.db.session import get_session_factory

    factory = get_session_factory()
    async with factory() as session:
        try:
            await PipelineEventRepository().update_status(
                session, UUID(pipeline_event_id), "failed"
            )
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class _TriagePipelineTask(Task):
    """Custom Task subclass so on_failure can be defined as a real method."""

    def on_failure(self, exc, task_id, args, kwargs, einfo):  # noqa: ANN001
        """Celery callback invoked once all retries are exhausted.

        Marks the corresponding PipelineEvent as 'failed' so the record is not
        silently abandoned in a 'triaging' or 'pending' state.
        """
        pipeline_event_id: str | None = args[0] if args else None

        if pipeline_event_id is None:
            logger.warning(
                "triage_pipeline.on_failure.no_event_id",
                task_id=task_id,
                error=str(exc),
            )
            return

        logger.error(
            "triage_pipeline.permanently_failed",
            pipeline_event_id=pipeline_event_id,
            task_id=task_id,
            error=str(exc),
        )

        try:
            asyncio.run(_mark_event_failed(pipeline_event_id))
        except Exception as db_exc:
            logger.error(
                "triage_pipeline.on_failure.db_update_failed",
                pipeline_event_id=pipeline_event_id,
                task_id=task_id,
                error=str(db_exc),
            )


@celery_app.task(bind=True, base=_TriagePipelineTask, max_retries=3, default_retry_delay=60)
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
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries)) from exc
