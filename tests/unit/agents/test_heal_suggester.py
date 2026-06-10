"""Tests for heal_suggester_node() — mocked LLM, real test DB."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.heal_suggester import HealSuggestionResult, heal_suggester_node
from src.agents.state import initial_state
from src.db.repositories.heal_suggestion_repo import HealSuggestionRepository
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure into the test DB and return the failure."""
    event = PipelineEvent(
        provider="jenkins",
        provider_build_id="build-2",
        repository="org/repo",
        branch="main",
        commit_sha="def456",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_db_connection",
        error_message="ConnectionError: DB pool exhausted after 30s",
        stack_trace="File src/db/session.py, line 88\nConnectionError: pool exhausted",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _make_mock_llm(
    suggestion: str = "Fix the pool size",
    confidence: float = 0.8,
    affected_file: str | None = "src/db/session.py",
    fix_snippet: str | None = "pool_size=20",
) -> MagicMock:
    """Return a fully-configured ChatAnthropic mock returning a HealSuggestionResult."""
    mock_result = HealSuggestionResult(
        suggestion=suggestion,
        confidence=confidence,
        affected_file=affected_file,
        fix_snippet=fix_snippet,
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


_SAMPLE_ROOT_CAUSE = {
    "root_cause_summary": "DB pool exhausted under load",
    "root_cause_category": "infra_flap",
    "likely_cause_files": ["src/db/session.py"],
    "investigation_steps": ["Check pool size", "Add connection limits"],
}

_HIGH_CONFIDENCE_CLASSIFICATION = {
    "category": "product_bug",
    "confidence": 0.85,
    "reasoning": "Assertion error in business logic",
}

_LOW_CONFIDENCE_CLASSIFICATION = {
    "category": "product_bug",
    "confidence": 0.7,
    "reasoning": "Assertion error in business logic",
}

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_heal_suggester_high_confidence(db_session: AsyncSession):
    """Node generates a suggestion when root_cause is present and confidence >= 0.8."""
    failure = await _make_failure(db_session)
    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure.id)],
        "root_cause": _SAMPLE_ROOT_CAUSE,
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
    }

    mock_llm_cls = _make_mock_llm(
        suggestion="Fix the pool size",
        confidence=0.8,
        affected_file="src/db/session.py",
        fix_snippet="pool_size=20",
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is not None
    assert result["heal_suggestion"]["suggestion"] == "Fix the pool size"

    # Verify the record was persisted to DB
    records = await HealSuggestionRepository().get_by_failure_id(db_session, failure.id)
    assert len(records) == 1


async def test_heal_suggester_skip_low_confidence(db_session: AsyncSession):
    """Node skips a failure (no suggestion, no LLM call) when ITS OWN
    classification confidence < 0.8, looked up via state["classifications"]."""
    failure = await _make_failure(db_session)
    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure.id)],
        "root_cause": _SAMPLE_ROOT_CAUSE,
        "classification": _LOW_CONFIDENCE_CLASSIFICATION,
        "classifications": {str(failure.id): _LOW_CONFIDENCE_CLASSIFICATION},
    }
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is None
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()

    records = await HealSuggestionRepository().get_by_failure_id(db_session, failure.id)
    assert records == []


async def test_heal_suggester_per_failure_confidence_in_multi_failure_run(
    db_session: AsyncSession,
):
    """In a multi-failure run, state["classification"] only holds the LAST
    failure's classification. A high-confidence failure must still get a
    suggestion even when the LAST-processed failure was low-confidence, and
    vice versa — each failure is gated on its OWN classification."""
    high_conf_failure = await _make_failure(db_session)
    low_conf_failure = await _make_failure(db_session)

    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(high_conf_failure.id), str(low_conf_failure.id)],
        "root_cause": _SAMPLE_ROOT_CAUSE,
        # Shared "last" field holds the LOW-confidence failure's classification —
        # must NOT suppress the suggestion for the high-confidence failure.
        "classification": _LOW_CONFIDENCE_CLASSIFICATION,
        "classifications": {
            str(high_conf_failure.id): _HIGH_CONFIDENCE_CLASSIFICATION,
            str(low_conf_failure.id): _LOW_CONFIDENCE_CLASSIFICATION,
        },
    }

    mock_llm_cls = _make_mock_llm(
        suggestion="Fix the pool size",
        confidence=0.8,
        affected_file="src/db/session.py",
        fix_snippet="pool_size=20",
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is not None
    assert result["heal_suggestion"]["suggestion"] == "Fix the pool size"

    high_conf_records = await HealSuggestionRepository().get_by_failure_id(
        db_session, high_conf_failure.id
    )
    assert len(high_conf_records) == 1

    low_conf_records = await HealSuggestionRepository().get_by_failure_id(
        db_session, low_conf_failure.id
    )
    assert low_conf_records == []


async def test_heal_suggester_per_failure_root_cause_in_multi_failure_run(
    db_session: AsyncSession,
):
    """In a multi-failure run, each failure's heal suggestion must be built from
    THAT failure's own root cause (looked up from state["root_causes"]), not
    from the shared state["root_cause"] which only holds the LAST failure's
    root cause analysis result."""
    failure_a = await _make_failure(db_session)
    failure_b = await _make_failure(db_session)

    root_cause_a = {
        "root_cause_summary": "DB pool exhausted under load",
        "root_cause_category": "infra_flap",
        "likely_cause_files": ["src/db/session.py"],
        "investigation_steps": ["Check pool size"],
    }
    root_cause_b = {
        "root_cause_summary": "Race condition in auth middleware",
        "root_cause_category": "code_regression",
        "likely_cause_files": ["src/auth/middleware.py"],
        "investigation_steps": ["Add locking"],
    }

    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure_a.id), str(failure_b.id)],
        # Shared "last" field holds failure B's root cause — must NOT be used
        # for failure A.
        "root_cause": root_cause_b,
        "root_causes": {
            str(failure_a.id): root_cause_a,
            str(failure_b.id): root_cause_b,
        },
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
        "classifications": {
            str(failure_a.id): _HIGH_CONFIDENCE_CLASSIFICATION,
            str(failure_b.id): _HIGH_CONFIDENCE_CLASSIFICATION,
        },
    }

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(
        return_value=HealSuggestionResult(
            suggestion="Fix it",
            confidence=0.8,
            affected_file="src/db/session.py",
            fix_snippet="pool_size=20",
        )
    )
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is not None
    assert mock_chain.ainvoke.await_count == 2

    user_messages = [
        call.args[0][1].content for call in mock_chain.ainvoke.call_args_list
    ]

    # First call is for failure_a -> must reference root_cause_a, not root_cause_b.
    assert "DB pool exhausted under load" in user_messages[0]
    assert "infra_flap" in user_messages[0]
    assert "Race condition in auth middleware" not in user_messages[0]

    # Second call is for failure_b -> must reference root_cause_b, not root_cause_a.
    assert "Race condition in auth middleware" in user_messages[1]
    assert "code_regression" in user_messages[1]
    assert "DB pool exhausted under load" not in user_messages[1]

    records_a = await HealSuggestionRepository().get_by_failure_id(db_session, failure_a.id)
    records_b = await HealSuggestionRepository().get_by_failure_id(db_session, failure_b.id)
    assert len(records_a) == 1
    assert len(records_b) == 1


async def test_heal_suggester_skips_failure_missing_its_own_root_cause(
    db_session: AsyncSession,
):
    """When state["root_causes"] is non-empty (so the global skip doesn't fire)
    but a SPECIFIC failure has no entry there and there's no shared
    state["root_cause"] fallback either, that failure is skipped (recorded as
    SKIPPED with reason "root_cause_missing") while other failures with their
    own root cause still get a suggestion."""
    failure_with_rc = await _make_failure(db_session)
    failure_without_rc = await _make_failure(db_session)

    root_cause_for_a = {
        "root_cause_summary": "DB pool exhausted under load",
        "root_cause_category": "infra_flap",
        "likely_cause_files": ["src/db/session.py"],
        "investigation_steps": ["Check pool size"],
    }

    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure_with_rc.id), str(failure_without_rc.id)],
        # No shared "last" root cause fallback.
        "root_cause": None,
        "root_causes": {str(failure_with_rc.id): root_cause_for_a},
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
        "classifications": {
            str(failure_with_rc.id): _HIGH_CONFIDENCE_CLASSIFICATION,
            str(failure_without_rc.id): _HIGH_CONFIDENCE_CLASSIFICATION,
        },
    }

    mock_llm_cls = _make_mock_llm(
        suggestion="Fix the pool size",
        confidence=0.8,
        affected_file="src/db/session.py",
        fix_snippet="pool_size=20",
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is not None

    # Only the LLM call for failure_with_rc happened.
    chain_mock = mock_llm_cls.return_value.with_structured_output.return_value
    assert chain_mock.ainvoke.await_count == 1

    records_with_rc = await HealSuggestionRepository().get_by_failure_id(
        db_session, failure_with_rc.id
    )
    records_without_rc = await HealSuggestionRepository().get_by_failure_id(
        db_session, failure_without_rc.id
    )
    assert len(records_with_rc) == 1
    assert records_without_rc == []


async def test_heal_suggester_skip_no_root_cause(db_session: AsyncSession):
    """Node skips and returns heal_suggestion=None when root_cause is None."""
    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(uuid.uuid4())],
        "root_cause": None,
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
    }
    mock_llm_cls = _make_mock_llm()

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is None
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()


async def test_heal_suggester_llm_exception(db_session: AsyncSession):
    """Node captures LLM errors into errors list without raising; heal_suggestion is None."""
    failure = await _make_failure(db_session)
    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure.id)],
        "root_cause": _SAMPLE_ROOT_CAUSE,
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
    }

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("model overloaded"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=_make_session_factory(db_session),
        ),
    ):
        result = await heal_suggester_node(state)

    assert result["heal_suggestion"] is None
    assert any("model overloaded" in err for err in result["errors"])


async def test_heal_suggester_db_write_verified(db_session: AsyncSession):
    """DB record has correct confidence and affected_file after a successful run."""
    failure = await _make_failure(db_session)
    state = {
        **initial_state("some-event-id"),
        "failure_ids": [str(failure.id)],
        "root_cause": _SAMPLE_ROOT_CAUSE,
        "classification": _HIGH_CONFIDENCE_CLASSIFICATION,
    }

    mock_llm_cls = _make_mock_llm(
        suggestion="Increase pool_size to 20",
        confidence=0.8,
        affected_file="src/db/session.py",
        fix_snippet="pool_size=20",
    )
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.heal_suggester.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.heal_suggester.get_session_factory",
            return_value=session_factory,
        ),
    ):
        await heal_suggester_node(state)

    records = await HealSuggestionRepository().get_by_failure_id(db_session, failure.id)
    assert len(records) == 1
    record = records[0]
    assert record.confidence == 0.8
    assert record.affected_file == "src/db/session.py"
