"""Tests for learner_node() — mocked FailureRepository and store_outcome_embedding."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from src.agents.nodes.learner import learner_node
from src.agents.state import initial_state

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FAKE_FAILURE_ID = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"


def _make_fake_failure(
    test_name: str = "test_foo",
    error_message: str = "boom",
    stack_trace: str = "",
) -> MagicMock:
    """Return a mock TestFailure ORM object."""
    failure = MagicMock()
    failure.test_name = test_name
    failure.error_message = error_message
    failure.stack_trace = stack_trace
    return failure


def _make_session_factory(failure: MagicMock | None = None):
    """Return a (session_factory, mock_failure_repo) pair.

    The session factory yields an AsyncMock session context manager, exactly
    as in test_notifier.py and test_ticket_creator.py.
    """
    mock_session = AsyncMock()

    mock_failure_repo = MagicMock()
    mock_failure_repo.get_by_id = AsyncMock(return_value=failure)

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    def _factory():
        return _ctx()

    return _factory, mock_failure_repo


def _build_state(
    failure_ids: list[str] | None = None,
    classification: dict | None = None,
    **overrides,
) -> dict:
    state = {
        **initial_state("test-event-id"),
        "failure_ids": failure_ids if failure_ids is not None else [],
        "classification": classification,
    }
    state.update(overrides)
    return state


_VALID_CLASSIFICATION = {
    "category": "product_bug",
    "confidence": 0.9,
    "reasoning": "test",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_learner_no_classification_returns_empty_dict():
    """Node returns {} immediately when classification is None.

    Neither the DB nor Qdrant should be touched.
    """
    state = _build_state(
        failure_ids=[_FAKE_FAILURE_ID],
        classification=None,
    )

    session_factory, mock_failure_repo = _make_session_factory()
    mock_store = AsyncMock()

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}
    mock_failure_repo.get_by_id.assert_not_called()
    mock_store.assert_not_called()


async def test_learner_no_failure_ids_returns_empty_dict():
    """Node returns {} immediately when failure_ids is empty."""
    state = _build_state(
        failure_ids=[],
        classification=_VALID_CLASSIFICATION,
    )

    session_factory, mock_failure_repo = _make_session_factory()
    mock_store = AsyncMock()

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}
    mock_store.assert_not_called()


async def test_learner_stores_outcome_for_each_failure_id():
    """Node calls store_outcome_embedding once per failure_id with the right args."""
    fake_failure = _make_fake_failure(
        test_name="test_foo",
        error_message="boom",
        stack_trace="",
    )
    state = _build_state(
        failure_ids=[_FAKE_FAILURE_ID],
        classification=_VALID_CLASSIFICATION,
        ticket_url="https://jira/PROJ-1",
    )

    session_factory, mock_failure_repo = _make_session_factory(fake_failure)
    mock_store = AsyncMock()

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}
    mock_store.assert_awaited_once()

    call_kwargs = mock_store.call_args
    assert call_kwargs.kwargs["point_id"] == _FAKE_FAILURE_ID
    payload = call_kwargs.kwargs["payload"]
    assert payload["category"] == "product_bug"
    assert payload["confidence"] == 0.9
    assert payload["test_name"] == "test_foo"


async def test_learner_uses_normalized_error_text_from_state():
    """When normalized_error_text is set in state, it is passed as error_text
    to store_outcome_embedding instead of the raw failure fields."""
    fake_failure = _make_fake_failure(
        test_name="test_foo",
        error_message="boom",
        stack_trace="",
    )
    state = _build_state(
        failure_ids=[_FAKE_FAILURE_ID],
        classification=_VALID_CLASSIFICATION,
        normalized_error_text="pre-normalized text",
    )

    session_factory, mock_failure_repo = _make_session_factory(fake_failure)
    mock_store = AsyncMock()

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}
    call_kwargs = mock_store.call_args
    assert call_kwargs.kwargs["error_text"] == "pre-normalized text"


async def test_learner_gracefully_handles_store_error():
    """When store_outcome_embedding raises, the node still returns {} without re-raising."""
    fake_failure = _make_fake_failure()
    state = _build_state(
        failure_ids=[_FAKE_FAILURE_ID],
        classification=_VALID_CLASSIFICATION,
    )

    session_factory, mock_failure_repo = _make_session_factory(fake_failure)
    mock_store = AsyncMock(side_effect=Exception("Qdrant down"))

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}


async def test_learner_gracefully_handles_failure_not_found():
    """When FailureRepository.get_by_id returns None, the node returns {} without raising."""
    state = _build_state(
        failure_ids=[_FAKE_FAILURE_ID],
        classification=_VALID_CLASSIFICATION,
    )

    # Repo returns None — failure record not found in DB.
    session_factory, mock_failure_repo = _make_session_factory(failure=None)
    mock_store = AsyncMock()

    with (
        patch(
            "src.agents.nodes.learner.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.learner.FailureRepository",
            return_value=mock_failure_repo,
        ),
        patch(
            "src.agents.nodes.learner.store_outcome_embedding",
            mock_store,
        ),
    ):
        result = await learner_node(state)

    assert result == {}
    mock_store.assert_not_called()
