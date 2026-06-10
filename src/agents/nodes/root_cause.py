from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.nodes.log_analyzer import normalize_error
from src.agents.nodes.run_tracking import finish_agent_run, start_agent_run
from src.agents.prompts.root_cause_prompt import ROOT_CAUSE_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.config.constants import AgentRunStatus
from src.config.settings import get_settings
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.root_cause_repo import RootCauseRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


class RootCauseResult(BaseModel):
    """Structured output returned by Claude for a single root cause analysis."""

    root_cause_summary: str = Field(
        ...,
        description="1-3 sentences describing the most likely root cause",
    )
    root_cause_category: str = Field(
        ...,
        description=(
            "One of: code_regression, infra_flap, config_drift, "
            "dependency_change, test_bug, unknown"
        ),
    )
    likely_cause_files: list[str] = Field(
        default_factory=list,
        description="File paths Claude suspects based on the stack trace; may be empty",
    )
    investigation_steps: list[str] = Field(
        ...,
        description="2-4 concrete actions an engineer should take to verify and fix the issue",
    )


async def root_cause_node(state: TriageState) -> dict:
    """Perform root cause analysis for each test failure in state['failure_ids'].

    For every failure:
      1. Load TestFailure from DB.
      2. Build a structured user message combining failure data with prior
         classification results.
      3. Invoke Claude with structured output to obtain a RootCauseResult.

    Root cause data is persisted to the root_cause_analyses table and also stored in state.
    Downstream nodes (ticket_creator, heal_suggester) consume it from state.

    Returns a partial state dict with 'root_cause' set to the last result's
    model_dump(), or None if all failures failed analysis.
    """
    log = logger.bind(
        node="root_cause",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("root_cause.started")

    if not state["failure_ids"]:
        log.warning("root_cause.no_failure_ids")
        return {
            "root_cause": None,
            "errors": state["errors"] + ["root_cause: no failure_ids in state"],
        }

    settings = get_settings()
    session_factory = get_session_factory()

    llm = ChatAnthropic(
        model=settings.default_model,
        api_key=settings.anthropic_api_key,
    )
    structured_llm = llm.with_structured_output(RootCauseResult)

    last_result: RootCauseResult | None = None
    classifications: dict[str, dict] = state.get("classifications") or {}
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        agent_run_id: uuid.UUID | None = None
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"root_cause: TestFailure not found: {failure_id}"
                    log.warning("root_cause.failure_not_found", failure_id=failure_id)
                    errors.append(msg)
                    continue

                agent_run_id = await start_agent_run(
                    session_factory,
                    test_failure_id=failure.id,
                    agent_name="root_cause",
                    input_summary=(
                        f"Test: {failure.test_name}\n"
                        f"Error: {(failure.error_message or 'N/A')[:200]}"
                    ),
                )

                # Prefer this failure's own classification (multi-failure runs only
                # retain the LAST failure's result in state["classification"]).
                classification = classifications.get(failure_id) or state.get("classification")
                clf_category = classification["category"] if classification else "unknown"
                clf_confidence = classification["confidence"] if classification else "N/A"
                clf_reasoning = classification["reasoning"] if classification else "N/A"

                # Recompute the normalized error text for THIS failure rather than
                # using state["normalized_error_text"], which only holds the last
                # failure's normalized text from log_analyzer's loop.
                error_text = (failure.error_message or "") + "\n" + (failure.stack_trace or "")
                norm_text = normalize_error(error_text)[:500]
                user_message = (
                    f"Test name: {failure.test_name}\n"
                    f"Error message: {failure.error_message or 'N/A'}\n"
                    f"Stack trace:\n{(failure.stack_trace or '')[:3000]}\n\n"
                    f"Classification: {clf_category}\n"
                    f"Confidence: {clf_confidence}\n"
                    f"Classification reasoning: {clf_reasoning}\n\n"
                    f"Error signature (normalized): {norm_text}"
                )

                result: RootCauseResult = await structured_llm.ainvoke(  # type: ignore[assignment]
                    [
                        SystemMessage(content=ROOT_CAUSE_SYSTEM_PROMPT),
                        HumanMessage(content=user_message),
                    ]
                )

                await RootCauseRepository().create(
                    session,
                    test_failure_id=uuid.UUID(failure_id),
                    pipeline_event_id=uuid.UUID(state["pipeline_event_id"]),
                    root_cause_summary=result.root_cause_summary,
                    root_cause_category=result.root_cause_category,
                    likely_cause_files=result.likely_cause_files,
                    investigation_steps=result.investigation_steps,
                    model_used=settings.default_model,
                )
                await session.commit()

                last_result = result
                log.info(
                    "root_cause.analyzed",
                    failure_id=failure_id,
                    root_cause_category=result.root_cause_category,
                )
                await finish_agent_run(
                    session_factory,
                    agent_run_id,
                    status=AgentRunStatus.COMPLETED,
                    output_summary=(
                        f"category={result.root_cause_category}\n"
                        f"{result.root_cause_summary}"
                    ),
                )

        except Exception as exc:
            msg = f"root_cause: error processing {failure_id}: {exc}"
            log.warning(
                "root_cause.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)
            last_result = None
            await finish_agent_run(
                session_factory,
                agent_run_id,
                status=AgentRunStatus.FAILED,
                output_summary=str(exc),
            )

    log.info("root_cause.complete")

    return {
        "root_cause": last_result.model_dump() if last_result else None,
        "errors": errors,
    }
