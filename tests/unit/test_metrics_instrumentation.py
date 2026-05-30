"""Unit tests verifying that Prometheus counter increments occur in the right
places:

  - FAILURES_RECEIVED is incremented by the webhook route handler.
  - TRIAGE_COMPLETED[status="success"] is incremented by run_triage_pipeline
    on a successful task execution.
  - TRIAGE_COMPLETED[status="failed"] is incremented when the task raises.
  - CLASSIFICATION_DISTRIBUTION[category=X] is incremented by
    failure_classifier_node after a successful Claude classification.

Approach
--------
Prometheus counters are process-global singletons.  Rather than resetting the
global registry (which affects other tests and can break metric name collision
guards), each test reads the *current* value before the action under test and
asserts that the value *increased by 1* afterwards.  This makes the tests
order-independent and safe to run in any sequence.

External services (Claude LLM, Celery, DB repositories) are always mocked.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.observability.metrics import (
    CLASSIFICATION_DISTRIBUTION,
    FAILURES_RECEIVED,
    TRIAGE_COMPLETED,
)

# ---------------------------------------------------------------------------
# Helpers — read Prometheus counter label values safely
# ---------------------------------------------------------------------------


def _counter_value(counter, **labels) -> float:
    """Return the current value of a labelled Prometheus counter.

    Falls back to 0.0 if the label combination has not been observed yet.
    """
    try:
        return counter.labels(**labels)._value.get()
    except Exception:
        return 0.0


# ===========================================================================
# FAILURES_RECEIVED counter — incremented in src/api/routes/webhooks.py
# ===========================================================================


@pytest.mark.asyncio
async def test_failures_received_incremented_after_successful_webhook(app_client):
    """FAILURES_RECEIVED[provider=jenkins] goes up by 1 after a valid webhook call."""
    before = _counter_value(FAILURES_RECEIVED, provider="jenkins")

    # Mock the WebhookService so it returns success without touching DB/Celery
    mock_result = {"pipeline_event_id": str(uuid.uuid4()), "status": "accepted"}
    with patch(
        "src.api.routes.webhooks.WebhookService"
    ) as MockService:
        mock_svc = MockService.return_value
        mock_svc.process_webhook = AsyncMock(return_value=mock_result)

        response = await app_client.post(
            "/api/v1/webhooks/jenkins",
            json={"build": "data"},
            headers={"Content-Type": "application/json"},
        )

    # 202 means the webhook route ran to completion (past the service call)
    assert response.status_code == 202
    after = _counter_value(FAILURES_RECEIVED, provider="jenkins")
    assert after == before + 1


@pytest.mark.asyncio
async def test_failures_received_uses_provider_from_path(app_client):
    """FAILURES_RECEIVED uses the URL path segment as the provider label."""
    before = _counter_value(FAILURES_RECEIVED, provider="github_actions")

    mock_result = {"pipeline_event_id": str(uuid.uuid4()), "status": "accepted"}
    with patch("src.api.routes.webhooks.WebhookService") as MockService:
        mock_svc = MockService.return_value
        mock_svc.process_webhook = AsyncMock(return_value=mock_result)

        response = await app_client.post(
            "/api/v1/webhooks/github_actions",
            json={},
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 202
    after = _counter_value(FAILURES_RECEIVED, provider="github_actions")
    assert after == before + 1


# ===========================================================================
# TRIAGE_COMPLETED counter — incremented in src/workers/tasks.py
#
# Celery task testing strategy:
#   run_triage_pipeline is a bind=True Celery task.  task.apply() executes the
#   task synchronously in the current process with a properly initialised fake
#   request context (self.request.id / self.request.retries are available).
#   We patch asyncio.run (which wraps the async triage call) to control the
#   outcome without needing a broker or event loop.
# ===========================================================================


def test_triage_completed_success_incremented_when_task_succeeds():
    """TRIAGE_COMPLETED[status=success] increments when run_triage completes."""
    from src.workers.tasks import run_triage_pipeline

    before = _counter_value(TRIAGE_COMPLETED, status="success")

    with patch("src.workers.tasks.asyncio.run", return_value={"status": "ok"}):
        result = run_triage_pipeline.apply(
            kwargs={"pipeline_event_id": str(uuid.uuid4())}
        )

    # .apply() returns an EagerResult; .get() retrieves the return value
    assert result.get() == {"status": "ok"}
    after = _counter_value(TRIAGE_COMPLETED, status="success")
    assert after == before + 1


def test_triage_completed_failed_incremented_when_task_raises():
    """TRIAGE_COMPLETED[status=failed] increments when run_triage raises.

    The task calls self.retry() on failure, which itself raises.  We patch
    asyncio.run to raise and also patch self.retry to re-raise immediately so
    the exception propagates out of apply() without needing a broker.
    """
    from src.workers.tasks import run_triage_pipeline

    before = _counter_value(TRIAGE_COMPLETED, status="failed")

    with (
        patch("src.workers.tasks.asyncio.run", side_effect=RuntimeError("DB exploded")),
        patch.object(
            run_triage_pipeline, "retry", side_effect=RuntimeError("DB exploded")
        ),
    ):
        result = run_triage_pipeline.apply(
            kwargs={"pipeline_event_id": str(uuid.uuid4())},
            throw=False,  # don't re-raise; inspect via result.state
        )

    # Task ended in failure — the counter must have been incremented
    after = _counter_value(TRIAGE_COMPLETED, status="failed")
    assert after == before + 1
    assert result.state == "FAILURE"


def test_triage_completed_success_not_incremented_when_task_fails():
    """Success counter must NOT increment when the task raises."""
    from src.workers.tasks import run_triage_pipeline

    before_success = _counter_value(TRIAGE_COMPLETED, status="success")

    with (
        patch("src.workers.tasks.asyncio.run", side_effect=ValueError("timeout")),
        patch.object(
            run_triage_pipeline, "retry", side_effect=ValueError("timeout")
        ),
    ):
        run_triage_pipeline.apply(
            kwargs={"pipeline_event_id": str(uuid.uuid4())},
            throw=False,
        )

    after_success = _counter_value(TRIAGE_COMPLETED, status="success")
    assert after_success == before_success  # unchanged


def test_triage_completed_failed_not_incremented_when_task_succeeds():
    """Failed counter must NOT increment when the task completes normally."""
    from src.workers.tasks import run_triage_pipeline

    before_failed = _counter_value(TRIAGE_COMPLETED, status="failed")

    with patch("src.workers.tasks.asyncio.run", return_value={"done": True}):
        run_triage_pipeline.apply(
            kwargs={"pipeline_event_id": str(uuid.uuid4())}
        )

    after_failed = _counter_value(TRIAGE_COMPLETED, status="failed")
    assert after_failed == before_failed  # unchanged


# ===========================================================================
# CLASSIFICATION_DISTRIBUTION counter — incremented in
# src/agents/nodes/failure_classifier.py
# ===========================================================================


@pytest.mark.asyncio
async def test_classification_distribution_incremented_with_correct_category():
    """CLASSIFICATION_DISTRIBUTION[category=product_bug] increments after classification."""
    from src.agents.nodes.failure_classifier import (
        ClassificationResult,
        failure_classifier_node,
    )
    from src.models.test_failure import TestFailure

    before = _counter_value(CLASSIFICATION_DISTRIBUTION, category="product_bug")

    mock_failure = MagicMock(spec=TestFailure)
    mock_failure.id = uuid.uuid4()
    mock_failure.test_name = "test_checkout_total"
    mock_failure.error_message = "AssertionError: expected 99.99 but got 0.00"
    mock_failure.stack_trace = "Traceback...\nAssertionError"

    mock_classification_result = ClassificationResult(
        category="product_bug",
        confidence=0.92,
        reasoning="Assertion error in business logic indicates a product defect",
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    failure_id = str(mock_failure.id)

    with (
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory"
        ) as mock_session_factory,
        patch(
            "src.agents.nodes.failure_classifier.FailureRepository"
        ) as MockFailureRepo,
        patch(
            "src.agents.nodes.failure_classifier.ClassificationRepository"
        ) as MockClassRepo,
        patch(
            "src.agents.nodes.failure_classifier.ChatAnthropic"
        ) as MockLLM,
    ):
        # session_factory() returns an async context manager (async with session_factory() as s)
        mock_session_factory.return_value = MagicMock(return_value=mock_session)

        # FailureRepository.get_by_id returns the mock failure
        MockFailureRepo.return_value.get_by_id = AsyncMock(return_value=mock_failure)
        MockFailureRepo.return_value.update_status = AsyncMock()

        # ClassificationRepository.upsert is a no-op
        MockClassRepo.return_value.upsert = AsyncMock()

        # ChatAnthropic structured LLM returns our predetermined result
        mock_llm_instance = MockLLM.return_value
        mock_structured = AsyncMock()
        mock_structured.ainvoke = AsyncMock(return_value=mock_classification_result)
        mock_llm_instance.with_structured_output.return_value = mock_structured

        state = {
            "pipeline_event_id": str(uuid.uuid4()),
            "failure_ids": [failure_id],
            "errors": [],
            "classification": None,
        }
        await failure_classifier_node(state)

    after = _counter_value(CLASSIFICATION_DISTRIBUTION, category="product_bug")
    assert after == before + 1


@pytest.mark.asyncio
async def test_classification_distribution_uses_returned_category_label():
    """The counter label matches exactly the category string returned by Claude."""
    from src.agents.nodes.failure_classifier import (
        ClassificationResult,
        failure_classifier_node,
    )
    from src.models.test_failure import TestFailure

    category = "flaky_test"
    before = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)

    mock_failure = MagicMock(spec=TestFailure)
    mock_failure.id = uuid.uuid4()
    mock_failure.test_name = "test_intermittent_login"
    mock_failure.error_message = "AssertionError: timeout waiting for element"
    mock_failure.stack_trace = "..."

    mock_classification_result = ClassificationResult(
        category=category,
        confidence=0.78,
        reasoning="Intermittent timing issue indicates flakiness",
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("src.agents.nodes.failure_classifier.get_session_factory") as mock_sf,
        patch("src.agents.nodes.failure_classifier.FailureRepository") as MockFR,
        patch("src.agents.nodes.failure_classifier.ClassificationRepository") as MockCR,
        patch("src.agents.nodes.failure_classifier.ChatAnthropic") as MockLLM,
    ):
        mock_sf.return_value = MagicMock(return_value=mock_session)
        MockFR.return_value.get_by_id = AsyncMock(return_value=mock_failure)
        MockFR.return_value.update_status = AsyncMock()
        MockCR.return_value.upsert = AsyncMock()

        mock_structured = AsyncMock()
        mock_structured.ainvoke = AsyncMock(return_value=mock_classification_result)
        MockLLM.return_value.with_structured_output.return_value = mock_structured

        state = {
            "pipeline_event_id": str(uuid.uuid4()),
            "failure_ids": [str(mock_failure.id)],
            "errors": [],
            "classification": None,
        }
        await failure_classifier_node(state)

    after = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)
    assert after == before + 1


@pytest.mark.asyncio
async def test_classification_distribution_not_incremented_when_failure_not_found():
    """Counter is NOT incremented when the TestFailure row is missing from DB."""
    from src.agents.nodes.failure_classifier import failure_classifier_node

    category = "env_issue"
    before = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    with (
        patch("src.agents.nodes.failure_classifier.get_session_factory") as mock_sf,
        patch("src.agents.nodes.failure_classifier.FailureRepository") as MockFR,
        patch("src.agents.nodes.failure_classifier.ChatAnthropic"),
    ):
        mock_sf.return_value = MagicMock(return_value=mock_session)
        # Simulate failure not found — returns None
        MockFR.return_value.get_by_id = AsyncMock(return_value=None)

        state = {
            "pipeline_event_id": str(uuid.uuid4()),
            "failure_ids": [str(uuid.uuid4())],
            "errors": [],
            "classification": None,
        }
        await failure_classifier_node(state)

    after = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)
    assert after == before  # unchanged


@pytest.mark.asyncio
async def test_classification_distribution_not_incremented_when_no_failure_ids():
    """Counter is NOT incremented when the state has no failure_ids."""
    from src.agents.nodes.failure_classifier import failure_classifier_node

    # Pick a distinct label so any prior test state does not interfere
    any_category = "timeout"
    before = _counter_value(CLASSIFICATION_DISTRIBUTION, category=any_category)

    state = {
        "pipeline_event_id": str(uuid.uuid4()),
        "failure_ids": [],  # empty — classifier exits early
        "errors": [],
        "classification": None,
    }
    result = await failure_classifier_node(state)

    # Should return an error entry but not increment the counter
    assert "errors" in result
    after = _counter_value(CLASSIFICATION_DISTRIBUTION, category=any_category)
    assert after == before  # unchanged


@pytest.mark.asyncio
async def test_classification_distribution_incremented_per_failure_id():
    """Counter increments once per classified failure when multiple IDs are given."""
    from src.agents.nodes.failure_classifier import (
        ClassificationResult,
        failure_classifier_node,
    )
    from src.models.test_failure import TestFailure

    category = "config_error"
    before = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)

    def _make_failure():
        m = MagicMock(spec=TestFailure)
        m.id = uuid.uuid4()
        m.test_name = "test_config"
        m.error_message = "KeyError: missing config value"
        m.stack_trace = "..."
        return m

    failure_a = _make_failure()
    failure_b = _make_failure()
    failure_ids = [str(failure_a.id), str(failure_b.id)]
    failures_by_id = {failure_a.id: failure_a, failure_b.id: failure_b}

    mock_classification_result = ClassificationResult(
        category=category, confidence=0.88, reasoning="Missing config key"
    )

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.commit = AsyncMock()

    async def _get_by_id(_session, fid):
        return failures_by_id.get(fid)

    with (
        patch("src.agents.nodes.failure_classifier.get_session_factory") as mock_sf,
        patch("src.agents.nodes.failure_classifier.FailureRepository") as MockFR,
        patch("src.agents.nodes.failure_classifier.ClassificationRepository") as MockCR,
        patch("src.agents.nodes.failure_classifier.ChatAnthropic") as MockLLM,
    ):
        mock_sf.return_value = MagicMock(return_value=mock_session)
        MockFR.return_value.get_by_id = AsyncMock(side_effect=_get_by_id)
        MockFR.return_value.update_status = AsyncMock()
        MockCR.return_value.upsert = AsyncMock()

        mock_structured = AsyncMock()
        mock_structured.ainvoke = AsyncMock(return_value=mock_classification_result)
        MockLLM.return_value.with_structured_output.return_value = mock_structured

        state = {
            "pipeline_event_id": str(uuid.uuid4()),
            "failure_ids": failure_ids,
            "errors": [],
            "classification": None,
        }
        await failure_classifier_node(state)

    after = _counter_value(CLASSIFICATION_DISTRIBUTION, category=category)
    # Two failures were classified — counter should have gone up by 2
    assert after == before + 2
