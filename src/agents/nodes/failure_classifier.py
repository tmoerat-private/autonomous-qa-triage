from __future__ import annotations

import uuid

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.prompts.classifier_prompt import CLASSIFIER_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.observability.metrics import CLASSIFICATION_DISTRIBUTION
from src.config.settings import get_settings
from src.db.repositories.classification_repo import ClassificationRepository
from src.db.repositories.failure_repo import FailureRepository
from src.db.session import get_session_factory

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
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
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

                user_message = (
                    f"Test: {failure.test_name}\n"
                    f"Error: {failure.error_message or 'N/A'}\n"
                    f"Stack trace:\n{(failure.stack_trace or '')[:2000]}"
                )

                result: ClassificationResult = await structured_llm.ainvoke(
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
                log.info(
                    "failure_classifier.classified",
                    failure_id=failure_id,
                    category=result.category,
                    confidence=result.confidence,
                )
                CLASSIFICATION_DISTRIBUTION.labels(category=result.category).inc()

        except Exception as exc:
            msg = f"failure_classifier: error processing {failure_id}: {exc}"
            log.warning(
                "failure_classifier.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)

    log.info(
        "failure_classifier.complete",
        processed=len(state["failure_ids"]),
        errors=len(errors) - len(state["errors"]),
    )

    return {
        "classification": last_result.model_dump() if last_result else None,
        "errors": errors,
    }
