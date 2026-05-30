"""Tests for src/agents/nodes/duplicate_detector.py.

All tests are pure unit tests — no real DB, no real Qdrant, no real embeddings.
The session factory, repositories, and vector tools are all mocked at the module
boundary so each test controls exactly what the node sees.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.nodes.duplicate_detector import duplicate_detector_node
from src.agents.state import initial_state


# ---------------------------------------------------------------------------
# Shared constants
# ---------------------------------------------------------------------------

_FAKE_SIG_ID = uuid.uuid4()
_FAKE_FAILURE_ID = str(uuid.uuid4())
_FAKE_VECTOR = [0.1] * 384


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_failure(
    test_name: str = "test_example",
    error_message: str = "AssertionError: expected True, got False",
    stack_trace: str = "Traceback...",
) -> MagicMock:
    """Return a mock TestFailure ORM object."""
    failure = MagicMock()
    failure.test_name = test_name
    failure.error_message = error_message
    failure.stack_trace = stack_trace
    return failure


def _make_signature(sig_id: uuid.UUID | None = None) -> MagicMock:
    """Return a mock ErrorSignature ORM object."""
    sig = MagicMock()
    sig.id = sig_id or _FAKE_SIG_ID
    return sig


def _make_session_factory(
    failure: MagicMock | None,
    sig: MagicMock,
    is_dup: bool,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build a fake session factory and the two repository mocks.

    Returns (session_factory, mock_failure_repo_instance, mock_sig_repo_instance).
    The factory yields a real async context manager wrapping an AsyncMock session.
    """
    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(return_value=None)

    mock_failure_repo = AsyncMock()
    mock_failure_repo.get_by_id = AsyncMock(return_value=failure)

    mock_sig_repo = AsyncMock()
    mock_sig_repo.get_or_create = AsyncMock(return_value=(sig, is_dup))
    mock_sig_repo.update_embedding_id = AsyncMock(return_value=sig)

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(return_value=_ctx())

    return factory, mock_failure_repo, mock_sig_repo


def _base_state(failure_ids: list[str]) -> dict:
    return {**initial_state("test-pipeline-id"), "failure_ids": failure_ids}


# ---------------------------------------------------------------------------
# Phase 1 — exact hash match
# ---------------------------------------------------------------------------


async def test_exact_hash_match_sets_is_duplicate_true():
    """When get_or_create returns is_dup=True, state has is_duplicate=True."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=True)

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
        ) as mock_find_similar,
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    assert result["is_duplicate"] is True
    assert result["duplicate_of_id"] == str(sig.id)
    # Exact match found — vector search must NOT be triggered.
    mock_find_similar.assert_not_awaited()


async def test_exact_hash_match_stores_embedding_even_for_duplicate():
    """store_error_embedding and update_embedding_id are called for every failure,
    including exact duplicates, to backfill any missing embeddings."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=True)

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ) as mock_store,
    ):
        await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    mock_store.assert_awaited_once()
    mock_sig_repo.update_embedding_id.assert_awaited_once()


# ---------------------------------------------------------------------------
# Phase 2 — vector similarity match
# ---------------------------------------------------------------------------


async def test_vector_similarity_match_sets_is_duplicate_true():
    """When get_or_create returns is_dup=False AND find_similar_errors returns a
    match, state has is_duplicate=True with the matching point ID."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=False)

    similar_result = [{"id": "qdrant-point-id-1", "score": 0.91, "payload": {}}]

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=similar_result,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    assert result["is_duplicate"] is True
    assert result["duplicate_of_id"] == "qdrant-point-id-1"


async def test_vector_similarity_match_uses_first_result():
    """When multiple similar results are returned, the first one's ID is used."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=False)

    similar_results = [
        {"id": "best-match", "score": 0.95, "payload": {}},
        {"id": "second-match", "score": 0.87, "payload": {}},
    ]

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=similar_results,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    assert result["duplicate_of_id"] == "best-match"


# ---------------------------------------------------------------------------
# No match at all
# ---------------------------------------------------------------------------


async def test_no_match_sets_is_duplicate_false():
    """When get_or_create returns is_dup=False AND find_similar_errors returns [],
    state has is_duplicate=False and duplicate_of_id=None."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=False)

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    assert result["is_duplicate"] is False
    assert result["duplicate_of_id"] is None


async def test_new_signature_calls_store_embedding_and_update_embedding_id():
    """For a new (non-duplicate) signature, store_error_embedding and
    update_embedding_id are both called so future searches can find it."""
    sig = _make_signature()
    failure = _make_failure()
    factory, mock_fail_repo, mock_sig_repo = _make_session_factory(failure, sig, is_dup=False)

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ) as mock_store,
    ):
        await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    mock_store.assert_awaited_once()
    mock_sig_repo.update_embedding_id.assert_awaited_once()


# ---------------------------------------------------------------------------
# Empty failure_ids guard
# ---------------------------------------------------------------------------


async def test_empty_failure_ids_returns_not_duplicate():
    """Node returns is_duplicate=False immediately when failure_ids is empty."""
    result = await duplicate_detector_node(_base_state([]))

    assert result["is_duplicate"] is False
    assert result["duplicate_of_id"] is None


# ---------------------------------------------------------------------------
# DB error handling
# ---------------------------------------------------------------------------


async def test_db_error_is_appended_to_errors_list():
    """An exception inside the loop is caught, added to errors, and
    is_duplicate stays False — the node never raises."""
    # Make the failure_repo raise to trigger the except branch.
    mock_fail_repo = AsyncMock()
    mock_fail_repo.get_by_id = AsyncMock(side_effect=RuntimeError("connection reset"))

    mock_sig_repo = AsyncMock()

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(return_value=_ctx())

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(_base_state([_FAKE_FAILURE_ID]))

    assert result["is_duplicate"] is False
    assert any("connection reset" in err for err in result["errors"])


async def test_db_error_does_not_prevent_processing_other_failures():
    """When one failure raises, subsequent failures are still processed."""
    failure_id_1 = str(uuid.uuid4())
    failure_id_2 = str(uuid.uuid4())

    sig = _make_signature()
    good_failure = _make_failure(error_message="NullPointerException")

    call_count = 0

    async def get_by_id_side_effect(session, failure_uuid):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("timeout on first call")
        return good_failure

    mock_fail_repo = AsyncMock()
    mock_fail_repo.get_by_id = AsyncMock(side_effect=get_by_id_side_effect)

    mock_sig_repo = AsyncMock()
    mock_sig_repo.get_or_create = AsyncMock(return_value=(sig, False))
    mock_sig_repo.update_embedding_id = AsyncMock(return_value=sig)

    mock_session = AsyncMock()
    mock_session.commit = AsyncMock(return_value=None)

    sessions = [mock_session, mock_session]
    session_iter = iter(sessions)

    @asynccontextmanager
    async def _ctx():
        yield next(session_iter, mock_session)

    factory = MagicMock(side_effect=_ctx)

    with (
        patch(
            "src.agents.nodes.duplicate_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.ErrorSignatureRepository",
            return_value=mock_sig_repo,
        ),
        patch(
            "src.agents.nodes.duplicate_detector.find_similar_errors",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "src.agents.nodes.duplicate_detector.store_error_embedding",
            new_callable=AsyncMock,
            return_value=_FAKE_VECTOR,
        ),
    ):
        result = await duplicate_detector_node(
            _base_state([failure_id_1, failure_id_2])
        )

    # First failed, second succeeded with no match.
    assert len(result["errors"]) == 1
    assert "timeout on first call" in result["errors"][0]
    # The second failure was processed and found no duplicate.
    assert result["is_duplicate"] is False
