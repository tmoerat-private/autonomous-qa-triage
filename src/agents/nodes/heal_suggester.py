from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.prompts.heal_suggester_prompt import HEAL_SUGGESTER_SYSTEM_PROMPT
from src.agents.state import TriageState
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

    Skip conditions (returns immediately with heal_suggestion=None):
      - state['root_cause'] is None: no root cause analysis was produced.
      - classification confidence < 0.8: insufficient certainty to suggest a fix.

    For every failure:
      1. Load TestFailure from DB.
      2. Build a structured user message combining failure data, root cause, and
         classification context.
      3. Invoke Claude with structured output to obtain a HealSuggestionResult.
      4. Persist the suggestion to the heal_suggestions table.

    Returns a partial state dict with 'heal_suggestion' set to the last result's
    model_dump(), or None if all failures failed or skipped.
    """
    log = logger.bind(
        node="heal_suggester",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("heal_suggester.started")

    # --- Skip conditions ---
    if state.get("root_cause") is None:
        log.info("heal_suggester.skipped", reason="root_cause_missing")
        return {"heal_suggestion": None}

    classification = state.get("classification") or {}
    if classification.get("confidence", 0) < 0.8:
        log.info(
            "heal_suggester.skipped",
            reason="classification_confidence_too_low",
            confidence=classification.get("confidence", 0),
        )
        return {"heal_suggestion": None}

    if not state["failure_ids"]:
        log.warning("heal_suggester.no_failure_ids")
        return {
            "heal_suggestion": None,
            "errors": state["errors"] + ["heal_suggester: no failure_ids in state"],
        }

    settings = get_settings()
    session_factory = get_session_factory()

    llm = ChatAnthropic(
        model=settings.default_model,
        api_key=settings.anthropic_api_key,
    )
    structured_llm = llm.with_structured_output(HealSuggestionResult)

    last_result: HealSuggestionResult | None = None
    errors: list[str] = list(state["errors"])

    root_cause = state["root_cause"]

    for failure_id in state["failure_ids"]:
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

                likely_files = ", ".join(root_cause["likely_cause_files"]) or "unknown"
                user_message = (
                    f"Test: {failure.test_name}\n"
                    f"Error: {failure.error_message or 'N/A'}\n"
                    f"Stack trace:\n{(failure.stack_trace or '')[:2000]}\n\n"
                    f"Root cause summary: {root_cause['root_cause_summary']}\n"
                    f"Root cause category: {root_cause['root_cause_category']}\n"
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

        except Exception as exc:
            msg = f"heal_suggester: error processing {failure_id}: {exc}"
            log.warning(
                "heal_suggester.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)

    log.info("heal_suggester.complete")

    return {
        "heal_suggestion": last_result.model_dump() if last_result else None,
        "errors": errors,
    }
