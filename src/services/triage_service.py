from __future__ import annotations

from uuid import UUID

import structlog

from src.agents.orchestrator import triage_graph
from src.agents.state import initial_state
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


async def run_triage(pipeline_event_id: str) -> dict:
    """Run the full LangGraph triage pipeline for a pipeline event.

    Called from the Celery task via asyncio.run().  Builds an initial
    TriageState, invokes the compiled graph, and returns the final state as a
    plain dict.  Marks the PipelineEvent as ``"triaged"`` on successful
    completion (or ``"failed"`` if the graph raises).

    Args:
        pipeline_event_id: UUID string of the PipelineEvent to triage.

    Returns:
        The final TriageState as a plain dict containing all node outputs.
    """
    log = logger.bind(pipeline_event_id=pipeline_event_id)
    log.info("triage_service.started")

    state = initial_state(pipeline_event_id)
    result = await triage_graph.ainvoke(state)

    log.info(
        "triage_service.completed",
        failure_ids=result.get("failure_ids", []),
        is_duplicate=result.get("is_duplicate", False),
        errors=result.get("errors", []),
    )

    # Mark the pipeline event as fully triaged now that the graph has finished.
    await _update_pipeline_status(pipeline_event_id, "triaged")

    return dict(result)


async def _update_pipeline_status(pipeline_event_id: str, status: str) -> None:
    """Open a fresh async DB session and update the pipeline event status."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            await PipelineEventRepository().update_status(
                session, UUID(pipeline_event_id), status
            )
            await session.commit()
            logger.info(
                "triage_service.pipeline_status_updated",
                pipeline_event_id=pipeline_event_id,
                status=status,
            )
        except Exception as exc:
            await session.rollback()
            logger.error(
                "triage_service.pipeline_status_update_failed",
                pipeline_event_id=pipeline_event_id,
                status=status,
                error=str(exc),
            )
