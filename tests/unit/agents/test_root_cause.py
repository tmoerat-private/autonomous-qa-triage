"""Tests for root_cause_node() — mocked LLM, real test DB."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from langchain_core.messages import HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.root_cause import RootCauseResult, root_cause_node
from src.agents.state import initial_state
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# Valid UUID used as pipeline_event_id in state (the node now calls uuid.UUID() on it)
_TEST_PIPELINE_EVENT_ID = "00000000-0000-0000-0000-000000000001"

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
        test_name="test_checkout_flow",
        error_message="ConnectionError: DB pool exhausted after 30s",
        stack_trace="File src/db/session.py, line 88\nConnectionError: pool exhausted",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _make_mock_llm(
    root_cause_summary: str = "DB pool exhausted",
    root_cause_category: str = "infra_flap",
    likely_cause_files: list[str] | None = None,
    investigation_steps: list[str] | None = None,
) -> MagicMock:
    """Return a fully-configured ChatAnthropic mock returning a RootCauseResult."""
    mock_result = RootCauseResult(
        root_cause_summary=root_cause_summary,
        root_cause_category=root_cause_category,
        likely_cause_files=likely_cause_files or ["src/db/session.py"],
        investigation_steps=investigation_steps or ["Check pool size"],
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


async def test_root_cause_success(db_session: AsyncSession):
    """Node analyzes a failure and returns root_cause dict with expected values."""
    failure = await _make_failure(db_session)
    # Use the real PipelineEvent id so the FK constraint on root_cause_analyses is satisfied
    state = {**initial_state(str(failure.pipeline_event_id)), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(
        root_cause_summary="DB pool exhausted",
        root_cause_category="infra_flap",
        likely_cause_files=["src/db/session.py"],
        investigation_steps=["Check pool size"],
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await root_cause_node(state)

    assert result["root_cause"] is not None
    assert result["root_cause"]["root_cause_summary"] == "DB pool exhausted"
    assert result["root_cause"]["root_cause_category"] == "infra_flap"
    assert result["root_cause"]["likely_cause_files"] == ["src/db/session.py"]
    assert result["root_cause"]["investigation_steps"] == ["Check pool size"]
    assert result.get("errors", []) == []


async def test_root_cause_empty_failure_ids(db_session: AsyncSession):
    """Node returns root_cause=None and never calls LLM when failure_ids is empty."""
    state = {**initial_state(_TEST_PIPELINE_EVENT_ID), "failure_ids": []}
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await root_cause_node(state)

    assert result["root_cause"] is None
    # LLM chain should never have been invoked
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()


async def test_root_cause_failure_not_found(db_session: AsyncSession):
    """Node appends an error and does not crash when failure UUID does not exist."""
    non_existent_id = str(uuid.uuid4())
    state = {**initial_state(_TEST_PIPELINE_EVENT_ID), "failure_ids": [non_existent_id]}
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await root_cause_node(state)

    assert any(non_existent_id in err for err in result["errors"])


async def test_root_cause_llm_exception(db_session: AsyncSession):
    """Node captures LLM timeout into errors without raising; root_cause is None."""
    failure = await _make_failure(db_session)
    # Use the real PipelineEvent id so the FK constraint on root_cause_analyses is satisfied
    state = {**initial_state(str(failure.pipeline_event_id)), "failure_ids": [str(failure.id)]}

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("timeout"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await root_cause_node(state)

    assert result["root_cause"] is None
    assert any("timeout" in err for err in result["errors"])


async def test_root_cause_includes_classification_context(db_session: AsyncSession):
    """HumanMessage passed to LLM contains classification category when present in state."""
    failure = await _make_failure(db_session)
    # Use the real PipelineEvent id so the FK constraint on root_cause_analyses is satisfied
    state = {
        **initial_state(str(failure.pipeline_event_id)),
        "failure_ids": [str(failure.id)],
        "classification": {
            "category": "product_bug",
            "confidence": 0.9,
            "reasoning": "Assertion failure in business logic",
        },
    }

    mock_llm_cls = _make_mock_llm()
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=session_factory,
        ),
    ):
        await root_cause_node(state)

    chain_mock = mock_llm_cls.return_value.with_structured_output.return_value
    messages_passed = chain_mock.ainvoke.call_args.args[0]

    # Second message (index 1) is the HumanMessage with dynamic failure context.
    human_msg = messages_passed[1]
    assert isinstance(human_msg, HumanMessage)
    assert "product_bug" in human_msg.content


async def test_root_cause_result_structure(db_session: AsyncSession):
    """Returned root_cause dict has all four expected keys."""
    failure = await _make_failure(db_session)
    # Use the real PipelineEvent id so the FK constraint on root_cause_analyses is satisfied
    state = {**initial_state(str(failure.pipeline_event_id)), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(
        root_cause_summary="Race condition in auth middleware",
        root_cause_category="code_regression",
        likely_cause_files=["src/auth/middleware.py"],
        investigation_steps=["Reproduce with two concurrent requests", "Add locking"],
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.root_cause.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.root_cause.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await root_cause_node(state)

    root_cause = result["root_cause"]
    assert root_cause is not None
    assert "root_cause_summary" in root_cause
    assert "root_cause_category" in root_cause
    assert "likely_cause_files" in root_cause
    assert "investigation_steps" in root_cause
