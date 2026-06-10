"""Learning & Memory Agent node.

Runs as the terminal LangGraph node (after notifier). For every failure in
state['failure_ids'] it stores a triage-outcome embedding in the dedicated
``triage_outcomes`` Qdrant collection so that the failure_classifier can
retrieve similar past outcomes as dynamic few-shot examples on future runs.

Graceful degradation: any exception inside the node is caught and logged so
the pipeline always reaches END cleanly.  A learner failure must never block
delivery of the Slack notification or Jira ticket that already happened.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from src.agents.nodes.run_tracking import (
    finish_agent_run,
    record_agent_runs,
    start_agent_run,
)
from src.agents.state import TriageState
from src.agents.tools.vector_tools import store_outcome_embedding
from src.config.constants import AgentRunStatus
from src.db.repositories.failure_repo import FailureRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


async def learner_node(state: TriageState) -> dict:
    """Persist triage outcomes as Qdrant embeddings for future few-shot retrieval."""
    log = logger.bind(
        node="learner",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("learner.started")

    session_factory = get_session_factory()

    classification = state.get("classification")
    if not classification:
        log.info("learner.no_classification_skipping")
        await record_agent_runs(
            session_factory,
            state["failure_ids"],
            agent_name="learner",
            status=AgentRunStatus.SKIPPED,
            output_summary="Skipped: no classification result to learn from",
        )
        return {}

    if not state["failure_ids"]:
        log.info("learner.no_failure_ids_skipping")
        return {}

    failure_repo = FailureRepository()

    for failure_id in state["failure_ids"]:
        agent_run_id: uuid.UUID | None = None
        try:
            async with session_factory() as session:
                failure = await failure_repo.get_by_id(session, uuid.UUID(failure_id))
                if failure is None:
                    log.warning("learner.failure_not_found", failure_id=failure_id)
                    continue

                # Prefer normalized text already in state (set by log_analyzer) to
                # avoid re-computing.  Fall back to raw concatenation if absent.
                error_text: str = (
                    state.get("normalized_error_text")
                    or (failure.error_message or "") + "\n" + (failure.stack_trace or "")
                )

                agent_run_id = await start_agent_run(
                    session_factory,
                    test_failure_id=failure.id,
                    agent_name="learner",
                    input_summary=f"Test: {failure.test_name}",
                )

                payload: dict = {
                    "test_name": failure.test_name,
                    "category": classification["category"],
                    "confidence": classification["confidence"],
                    "reasoning": classification["reasoning"],
                    "ticket_url": state.get("ticket_url"),
                    "repository": state.get("repository"),
                    "stored_at": datetime.now(UTC).isoformat(),
                }

                await store_outcome_embedding(
                    point_id=failure_id,
                    error_text=error_text,
                    payload=payload,
                )
                log.info(
                    "learner.outcome_stored",
                    failure_id=failure_id,
                    category=classification["category"],
                )
                await finish_agent_run(
                    session_factory,
                    agent_run_id,
                    status=AgentRunStatus.COMPLETED,
                    output_summary=(
                        f"Stored outcome embedding (category={classification['category']})"
                    ),
                )

        except Exception as exc:
            # Non-fatal: learner failure must never block pipeline completion.
            log.warning("learner.error", failure_id=failure_id, error=str(exc))
            await finish_agent_run(
                session_factory,
                agent_run_id,
                status=AgentRunStatus.FAILED,
                output_summary=str(exc),
            )

    log.info("learner.complete", stored=len(state["failure_ids"]))
    return {}
