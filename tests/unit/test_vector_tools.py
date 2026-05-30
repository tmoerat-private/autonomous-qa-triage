"""Tests for src/agents/tools/vector_tools.py.

All tests are pure unit tests: neither SentenceTransformer nor QdrantManager
is ever instantiated — both are patched at the module boundary.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.tools.vector_tools import (
    find_similar_errors,
    generate_embedding,
    store_error_embedding,
)

# Fixed embedding vector used across all tests.
_FAKE_VECTOR: list[float] = [0.1] * 384


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_manager(
    find_similar_result: list[dict] | None = None,
) -> AsyncMock:
    """Return an AsyncMock QdrantManager with sensible defaults."""
    manager = AsyncMock()
    manager.ensure_collection = AsyncMock(return_value=None)
    manager.store_embedding = AsyncMock(return_value=None)
    manager.find_similar = AsyncMock(return_value=find_similar_result or [])
    return manager


# ---------------------------------------------------------------------------
# generate_embedding
# ---------------------------------------------------------------------------


async def test_generate_embedding_returns_list_of_floats():
    """generate_embedding returns a plain list of floats."""
    with patch(
        "src.agents.tools.vector_tools._embed_sync",
        return_value=_FAKE_VECTOR,
    ):
        result = await generate_embedding("some error text")

    assert isinstance(result, list)
    assert len(result) == 384
    assert all(isinstance(v, float) for v in result)


async def test_generate_embedding_correct_value():
    """generate_embedding passes _embed_sync's return value through unchanged."""
    expected = [0.5] * 384
    with patch(
        "src.agents.tools.vector_tools._embed_sync",
        return_value=expected,
    ):
        result = await generate_embedding("error")

    assert result == expected


async def test_generate_embedding_uses_to_thread():
    """generate_embedding delegates CPU work via asyncio.to_thread, not a direct call."""
    with patch("asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = _FAKE_VECTOR
        result = await generate_embedding("cpu-bound text")

    mock_to_thread.assert_awaited_once()
    # First positional arg must be the synchronous _embed_sync callable.
    called_fn = mock_to_thread.call_args.args[0]
    assert callable(called_fn)
    # Second positional arg is the text passed down.
    called_text = mock_to_thread.call_args.args[1]
    assert called_text == "cpu-bound text"
    assert result == _FAKE_VECTOR


# ---------------------------------------------------------------------------
# store_error_embedding
# ---------------------------------------------------------------------------


async def test_store_error_embedding_calls_ensure_collection():
    """store_error_embedding ensures the collection exists before upserting."""
    mock_manager = _make_mock_manager()
    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        await store_error_embedding(
            point_id="test-point-id",
            error_text="AssertionError: expected 1, got 0",
            payload={"key": "value"},
        )

    mock_manager.ensure_collection.assert_awaited_once_with(vector_size=384)


async def test_store_error_embedding_calls_store_with_correct_args():
    """store_error_embedding passes point_id, vector, and payload to store_embedding."""
    mock_manager = _make_mock_manager()
    payload = {"signature_hash": "abc123", "normalized_error": "some error"}

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        await store_error_embedding(
            point_id="point-abc",
            error_text="some error",
            payload=payload,
        )

    mock_manager.store_embedding.assert_awaited_once_with(
        point_id="point-abc",
        vector=_FAKE_VECTOR,
        payload=payload,
    )


async def test_store_error_embedding_returns_vector():
    """store_error_embedding returns the generated embedding vector."""
    mock_manager = _make_mock_manager()

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        result = await store_error_embedding(
            point_id="pid",
            error_text="text",
            payload={},
        )

    assert result == _FAKE_VECTOR


async def test_store_error_embedding_generates_embedding_internally():
    """store_error_embedding calls generate_embedding (not _embed_sync directly)
    to build the vector that is passed to store_embedding."""
    mock_manager = _make_mock_manager()
    custom_vector = [0.9] * 384

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=custom_vector),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        returned = await store_error_embedding(
            point_id="pid",
            error_text="text",
            payload={},
        )

    # The vector stored and the vector returned must both reflect what
    # generate_embedding produced (which calls _embed_sync internally).
    _, kwargs = mock_manager.store_embedding.call_args
    assert kwargs["vector"] == custom_vector
    assert returned == custom_vector


# ---------------------------------------------------------------------------
# find_similar_errors
# ---------------------------------------------------------------------------


async def test_find_similar_errors_calls_ensure_collection():
    """find_similar_errors ensures the collection exists before querying."""
    mock_manager = _make_mock_manager(find_similar_result=[])
    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        await find_similar_errors("some error", limit=5, score_threshold=0.85)

    mock_manager.ensure_collection.assert_awaited_once_with(vector_size=384)


async def test_find_similar_errors_calls_find_similar_with_correct_args():
    """find_similar_errors forwards limit and score_threshold to manager.find_similar."""
    mock_manager = _make_mock_manager(find_similar_result=[])
    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        await find_similar_errors("error text", limit=3, score_threshold=0.90)

    mock_manager.find_similar.assert_awaited_once_with(
        query_vector=_FAKE_VECTOR,
        limit=3,
        score_threshold=0.90,
    )


async def test_find_similar_errors_returns_manager_results():
    """find_similar_errors returns whatever manager.find_similar returns."""
    expected = [
        {"id": "sig-uuid-1", "score": 0.93, "payload": {"normalized_error": "err"}},
        {"id": "sig-uuid-2", "score": 0.88, "payload": {"normalized_error": "err2"}},
    ]
    mock_manager = _make_mock_manager(find_similar_result=expected)

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        result = await find_similar_errors("error", limit=5, score_threshold=0.85)

    assert result == expected


async def test_find_similar_errors_no_matches_returns_empty_list():
    """find_similar_errors returns an empty list when manager returns no results."""
    mock_manager = _make_mock_manager(find_similar_result=[])

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        result = await find_similar_errors("error text")

    assert result == []


async def test_find_similar_errors_uses_default_threshold():
    """find_similar_errors uses 0.85 as default score_threshold."""
    mock_manager = _make_mock_manager(find_similar_result=[])

    with (
        patch("src.agents.tools.vector_tools._embed_sync", return_value=_FAKE_VECTOR),
        patch(
            "src.agents.tools.vector_tools.get_qdrant_manager",
            return_value=mock_manager,
        ),
    ):
        await find_similar_errors("error text")

    _, kwargs = mock_manager.find_similar.call_args
    assert kwargs["score_threshold"] == 0.85
