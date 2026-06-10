from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.nodes.run_tracking import (
    finish_agent_run,
    record_agent_runs,
    start_agent_run,
)
from src.agents.prompts.heal_suggester_prompt import HEAL_SUGGESTER_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.config.constants import AgentRunStatus
from src.config.settings import get_settings
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.heal_suggestion_repo import HealSuggestionRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


class HealSuggestionResult(BaseModel):
    """Structured output returned by Claude for a single heal suggestion."""

    suggestion: str = Field(
        ...,
        description="Plain-English description of the fix in 1-3 sentences",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 to 1.0",
    )
    affected_file: str | None = Field(
        default=None,
        description="The file path most likely to change, or null if unknown",
    )
    fix_snippet: str | None = Field(
        default=None,
        description="A code snippet, diff, or specific line to change; null if too uncertain",
    )


async def heal_suggester_node(state: TriageState) -> dict:
    """Generate a concrete fix suggestion for each test failure in state['failure_ids'].

    Skip conditions:
      - state['root_causes'] is empty/missing AND state['root_cause'] is None:
        no root cause analysis was produced for ANY failure — the entire node
        is skipped (returns immediately with heal_suggestion=None).
      - Per-failure: this failure's own root cause is missing (looked up from
        state['root_causes'], falling back to the shared state['root_cause'])
        — that failure is skipped but others continue.
      - Per-failure: this failure's own classification confidence < 0.8 —
        insufficient certainty to suggest a fix for THIS failure. Each failure
        is evaluated against its own classification (looked up from
        state['classifications']), not a single shared value, so one
        low-confidence failure in a multi-failure run does not suppress
        suggestions for higher-confidence failures.

    For every (non-skipped) failure:
      1. Load TestFailure from DB.
      2. Build a structured user message combining failure data, this failure's
         own root cause (looked up from state['root_causes']), and this
         failure's own classification context.
      3. Invoke Claude with structured output to obtain a HealSuggestionResult.
      4. Persist the suggestion to the heal_suggestions table.

    Returns a partial state dict with 'heal_suggestion' set to the last result's
    model_dump(), or None if all failures failed or were skipped.
    """
    log = logger.bind(
        node="heal_suggester",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("heal_suggester.started")

    session_factory = get_session_factory()

    # --- Skip conditions ---
    root_cause: dict | None = state.get("root_cause")
    if not state.get("root_causes") and root_cause is None:
        log.info("heal_suggester.skipped", reason="root_cause_missing")
        await record_agent_runs(
            session_factory,
            state["failure_ids"],
            agent_name="heal_suggester",
            status=AgentRunStatus.SKIPPED,
            output_summary="Skipped: no root cause analysis available",
        )
        return {"heal_suggestion": None}

    if not state["failure_ids"]:
        log.warning("heal_suggester.no_failure_ids")
        return {
            "heal_suggestion": None,
            "errors": state["errors"] + ["heal_suggester: no failure_ids in state"],
        }

    settings = get_settings()

    llm = ChatAnthropic(
        model=settings.default_model,
        api_key=settings.anthropic_api_key,
    )
    structured_llm = llm.with_structured_output(HealSuggestionResult)

    last_result: HealSuggestionResult | None = None
    classifications: dict[str, dict] = state.get("classifications") or {}
    root_causes: dict[str, dict] = state.get("root_causes") or {}
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        agent_run_id: uuid.UUID | None = None
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"heal_suggester: TestFailure not found: {failure_id}"
                    log.warning("heal_suggester.failure_not_found", failure_id=failure_id)
                    errors.append(msg)
                    continue

                # Use THIS failure's own root cause, not the shared
                # state["root_cause"] (which only holds the last failure's
                # result from root_cause's loop).
                failure_root_cause = root_causes.get(failure_id) or root_cause
                if failure_root_cause is None:
                    log.info(
                        "heal_suggester.skipped",
                        reason="root_cause_missing",
                        failure_id=failure_id,
                    )
                    await record_agent_runs(
                        session_factory,
                        [failure_id],
                        agent_name="heal_suggester",
                        status=AgentRunStatus.SKIPPED,
                        output_summary="Skipped: no root cause for this failure",
                    )
                    continue

                # Use THIS failure's own classification, not the shared
                # state["classification"] (which only holds the last failure's
                # result from failure_classifier's loop).
                classification = classifications.get(failure_id) or state.get(
                    "classification"
                ) or {}
                confidence = classification.get("confidence", 0)
                if confidence < 0.8:
                    log.info(
                        "heal_suggester.skipped",
                        reason="classification_confidence_too_low",
                        failure_id=failure_id,
                        confidence=confidence,
                    )
                    await record_agent_runs(
                        session_factory,
                        [failure_id],
                        agent_name="heal_suggester",
                        status=AgentRunStatus.SKIPPED,
                        output_summary=(
                            f"Skipped: classification confidence {confidence:.2f} < 0.8"
                        ),
                    )
                    continue

                agent_run_id = await start_agent_run(
                    session_factory,
                    test_failure_id=failure.id,
                    agent_name="heal_suggester",
                    input_summary=(
                        f"Test: {failure.test_name}\n"
                        f"Root cause: {failure_root_cause['root_cause_summary']}"
                    ),
                )

                likely_files = (
                    ", ".join(failure_root_cause["likely_cause_files"]) or "unknown"
                )
                user_message = (
                    f"Test: {failure.test_name}\n"
                    f"Error: {failure.error_message or 'N/A'}\n"
                    f"Stack trace:\n{(failure.stack_trace or '')[:2000]}\n\n"
                    f"Root cause summary: {failure_root_cause['root_cause_summary']}\n"
                    f"Root cause category: {failure_root_cause['root_cause_category']}\n"
                    f"Likely cause files: {likely_files}\n\n"
                    f"Classification: {classification['category']} "
                    f"(confidence: {classification['confidence']:.2f})"
                )

                result: HealSuggestionResult = await structured_llm.ainvoke(  # type: ignore[assignment]
                    [
                        SystemMessage(content=HEAL_SUGGESTER_SYSTEM_PROMPT),
                        HumanMessage(content=user_message),
                    ]
                )

                await HealSuggestionRepository().create(
                    session,
                    test_failure_id=failure.id,
                    suggestion=result.suggestion,
                    confidence=result.confidence,
                    affected_file=result.affected_file,
                    fix_snippet=result.fix_snippet,
                    model_used=settings.default_model,
                    tokens_used=None,
                )
                await session.commit()

                last_result = result
                log.info(
                    "heal_suggester.suggested",
                    failure_id=failure_id,
                    confidence=result.confidence,
                    affected_file=result.affected_file,
                )
                await finish_agent_run(
                    session_factory,
                    agent_run_id,
                    status=AgentRunStatus.COMPLETED,
                    output_summary=(
                        f"confidence={result.confidence:.2f} "
                        f"affected_file={result.affected_file or 'unknown'}\n"
                        f"{result.suggestion}"
                    ),
                )

        except Exception as exc:
            msg = f"heal_suggester: error processing {failure_id}: {exc}"
            log.warning(
                "heal_suggester.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)
            await finish_agent_run(
                session_factory,
                agent_run_id,
                status=AgentRunStatus.FAILED,
                output_summary=str(exc),
            )

    log.info("heal_suggester.complete")

    return {
        "heal_suggestion": last_result.model_dump() if last_result else None,
        "errors": errors,
    }
