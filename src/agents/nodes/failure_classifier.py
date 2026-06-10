from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.nodes.log_analyzer import normalize_error
from src.agents.nodes.run_tracking import finish_agent_run, start_agent_run
from src.agents.prompts.classifier_prompt import CLASSIFIER_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.agents.tools.vector_tools import find_similar_outcomes
from src.config.constants import AgentRunStatus
from src.config.settings import get_settings
from src.db.repositories.classification_repo import ClassificationRepository
from src.db.repositories.failure_repo import FailureRepository
from src.db.session import get_session_factory
from src.observability.metrics import CLASSIFICATION_DISTRIBUTION

logger = structlog.get_logger(__name__)


class ClassificationResult(BaseModel):
    """Structured output returned by Claude for a single failure classification."""

    category: str = Field(
        ...,
        description="One of the FailureCategory enum values: product_bug, flaky_test, "
        "env_issue, timeout, infra_issue, config_error, dependency_failure",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    reasoning: str


async def failure_classifier_node(state: TriageState) -> dict:
    """Classify each test failure in state['failure_ids'] using Claude.

    For every failure:
      1. Load TestFailure from DB.
      2. Invoke Claude with structured output to obtain a ClassificationResult.
      3. Upsert the classification into failure_classifications.
      4. Advance the failure status to 'triaging'.

    Returns a partial state dict with 'classification' set to the last result,
    'classifications' mapping every successfully-classified failure_id to its
    own result (so downstream nodes can look up each failure's own
    classification instead of relying on the shared 'classification' field),
    or appends to 'errors' if no failure_ids are present.
    """
    log = logger.bind(
        node="failure_classifier",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("failure_classifier.started")

    if not state["failure_ids"]:
        log.warning("failure_classifier.no_failure_ids")
        return {
            "errors": state["errors"] + ["failure_classifier: no failure_ids in state"]
        }

    settings = get_settings()
    session_factory = get_session_factory()

    llm = ChatAnthropic(
        model=settings.default_model,
        api_key=settings.anthropic_api_key,
    )
    structured_llm = llm.with_structured_output(ClassificationResult)

    last_result: ClassificationResult | None = None
    classifications: dict[str, dict] = {}
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        agent_run_id: uuid.UUID | None = None
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"failure_classifier: TestFailure not found: {failure_id}"
                    log.warning("failure_classifier.failure_not_found", failure_id=failure_id)
                    errors.append(msg)
                    continue

                agent_run_id = await start_agent_run(
                    session_factory,
                    test_failure_id=failure.id,
                    agent_name="failure_classifier",
                    input_summary=(
                        f"Test: {failure.test_name}\n"
                        f"Error: {(failure.error_message or 'N/A')[:200]}"
                    ),
                )

                # --- Dynamic few-shot from Learning & Memory Agent ---
                dynamic_few_shot = ""
                try:
                    raw_error = (failure.error_message or "") + "\n" + (failure.stack_trace or "")
                    normalized = normalize_error(raw_error)
                    similar_outcomes = await find_similar_outcomes(normalized)
                    if similar_outcomes:
                        examples = []
                        for i, outcome in enumerate(similar_outcomes, 1):
                            p = outcome["payload"]
                            examples.append(
                                f"### Past Example {i} (similarity: {outcome['score']:.2f})\n"
                                f"Test: {p.get('test_name', 'unknown')}\n"
                                f"Classification: {p.get('category')} "
                                f"(confidence {p.get('confidence', 0):.2f})\n"
                                f"Reasoning: {p.get('reasoning', '')}"
                            )
                        dynamic_few_shot = (
                            "\n\n## Retrieved Similar Cases\n"
                            + "\n\n".join(examples)
                            + "\n"
                        )
                        log.info(
                            "failure_classifier.few_shot_retrieved",
                            failure_id=failure_id,
                            count=len(similar_outcomes),
                        )
                except Exception as exc:
                    # Non-fatal: proceed with the static prompt if Qdrant is unavailable.
                    log.warning(
                        "failure_classifier.few_shot_error",
                        failure_id=failure_id,
                        error=str(exc),
                    )

                user_message = (
                    f"Test: {failure.test_name}\n"
                    f"Error: {failure.error_message or 'N/A'}\n"
                    f"Stack trace:\n{(failure.stack_trace or '')[:2000]}"
                    f"{dynamic_few_shot}"
                )

                result: ClassificationResult = await structured_llm.ainvoke(  # type: ignore[assignment]
                    [
                        SystemMessage(content=CLASSIFIER_SYSTEM_PROMPT),
                        HumanMessage(content=user_message),
                    ]
                )

                await ClassificationRepository().upsert(
                    session,
                    test_failure_id=failure.id,
                    category=result.category,
                    confidence=result.confidence,
                    reasoning=result.reasoning,
                    model_used=settings.default_model,
                )
                await FailureRepository().update_status(session, failure.id, "triaging")
                await session.commit()

                last_result = result
                classifications[failure_id] = result.model_dump()
                log.info(
                    "failure_classifier.classified",
                    failure_id=failure_id,
                    category=result.category,
                    confidence=result.confidence,
                )
                CLASSIFICATION_DISTRIBUTION.labels(category=result.category).inc()
                await finish_agent_run(
                    session_factory,
                    agent_run_id,
                    status=AgentRunStatus.COMPLETED,
                    output_summary=(
                        f"category={result.category} confidence={result.confidence:.2f}\n"
                        f"{result.reasoning}"
                    ),
                )

        except Exception as exc:
            msg = f"failure_classifier: error processing {failure_id}: {exc}"
            log.warning(
                "failure_classifier.error",
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

    log.info(
        "failure_classifier.complete",
        processed=len(state["failure_ids"]),
        errors=len(errors) - len(state["errors"]),
    )

    return {
        "classification": last_result.model_dump() if last_result else None,
        "classifications": classifications,
        "errors": errors,
    }
