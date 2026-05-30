from __future__ import annotations

import structlog

from src.agents.orchestrator import triage_graph
from src.agents.state import initial_state

logger = structlog.get_logger(__name__)


async def run_triage(pipeline_event_id: str) -> dict:
    """Run the full LangGraph triage pipeline for a pipeline event.

    Called from the Celery task via asyncio.run().  Builds an initial
    TriageState, invokes the compiled graph, and returns the final state as a
    plain dict.

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

    return dict(result)
