"""Tests for failure_classifier_node() — mocked LLM, real test DB."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.failure_classifier import ClassificationResult, failure_classifier_node
from src.agents.state import initial_state
from src.db.repositories.classification_repo import ClassificationRepository
from src.db.repositories.failure_repo import FailureRepository
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure into the test DB and return the failure."""
    event = PipelineEvent(
        provider="jenkins",
        provider_build_id="build-1",
        repository="org/repo",
        branch="main",
        commit_sha="abc123",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_login",
        error_message="AssertionError: Expected 200, got 500",
        stack_trace="File test.py, line 42\nAssertionError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _make_mock_llm(category: str = "product_bug", confidence: float = 0.85) -> MagicMock:
    """Return a fully-configured ChatAnthropic mock."""
    mock_result = ClassificationResult(
        category=category,
        confidence=confidence,
        reasoning="Assertion error in business logic indicates a product defect",
    )
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_result)

    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain

    mock_cls = MagicMock(return_value=mock_llm_instance)
    return mock_cls


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_classifier_happy_path(db_session: AsyncSession):
    """Node classifies a failure and persists a FailureClassification record."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(category="product_bug", confidence=0.85)
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await failure_classifier_node(state)

    classification = await ClassificationRepository().get_by_failure_id(
        db_session, failure.id
    )
    assert classification is not None
    assert classification.category == "product_bug"
    assert result.get("errors", []) == []


async def test_classifier_empty_failure_ids(db_session: AsyncSession):
    """Node returns an error and makes no LLM call when failure_ids is empty."""
    state = {**initial_state("some-event-id"), "failure_ids": []}
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await failure_classifier_node(state)

    assert len(result["errors"]) > 0
    # with_structured_output chain should never have been invoked
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()


async def test_classifier_failure_not_found(db_session: AsyncSession):
    """Node appends an error and does not crash when the failure UUID does not exist."""
    non_existent_id = str(uuid.uuid4())
    state = {**initial_state("some-event-id"), "failure_ids": [non_existent_id]}
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await failure_classifier_node(state)

    assert any(non_existent_id in err for err in result["errors"])


async def test_classifier_llm_exception(db_session: AsyncSession):
    """Node captures LLM errors into the errors list without raising."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("API error"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await failure_classifier_node(state)

    assert any("API error" in err for err in result["errors"])


async def test_classifier_updates_failure_status(db_session: AsyncSession):
    """After the node runs, the TestFailure status is updated to 'triaging'."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm()
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=session_factory,
        ),
    ):
        await failure_classifier_node(state)

    reloaded = await FailureRepository().get_by_id(db_session, failure.id)
    assert reloaded is not None
    assert reloaded.status == "triaging"


async def test_classifier_result_in_state(db_session: AsyncSession):
    """The returned dict has a 'classification' key with the expected structure."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(category="product_bug", confidence=0.85)
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.failure_classifier.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await failure_classifier_node(state)

    classification = result.get("classification")
    assert classification is not None
    assert classification["category"] == "product_bug"
    assert classification["confidence"] == 0.85
    assert "reasoning" in classification
