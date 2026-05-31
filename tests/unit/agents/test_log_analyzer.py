"""Tests for normalize_error(), compute_signature(), and log_analyzer_node()."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.log_analyzer import compute_signature, log_analyzer_node, normalize_error
from src.agents.state import initial_state
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# normalize_error — individual normalization steps
# ---------------------------------------------------------------------------


def test_ansi_codes_stripped():
    result = normalize_error("\x1b[31mERROR\x1b[0m")
    assert result == "ERROR"


def test_iso_timestamp_stripped():
    result = normalize_error("2024-01-15T10:30:00.123Z error")
    assert result == "error"


def test_time_timestamp_stripped():
    result = normalize_error("at 10:30:00 error")
    assert result == "at error"


def test_memory_address_stripped():
    result = normalize_error("at 0x7f8a1b2c error")
    assert result == "at error"


def test_line_number_stripped():
    result = normalize_error("File test.py, line 42")
    assert result == "File test.py,"


def test_uuid_stripped():
    result = normalize_error("id=550e8400-e29b-41d4-a716-446655440000 error")
    assert result == "id= error"


def test_multiple_normalizations():
    """ANSI codes, ISO timestamp, and UUID all stripped in a single pass."""
    raw = (
        "\x1b[31m2024-01-15T10:30:00Z\x1b[0m "
        "session=550e8400-e29b-41d4-a716-446655440000 failed"
    )
    result = normalize_error(raw)
    # No ANSI codes
    assert "\x1b" not in result
    # No ISO timestamp
    assert "2024-01-15T10:30:00Z" not in result
    # No UUID
    assert "550e8400-e29b-41d4-a716-446655440000" not in result
    # Meaningful words remain
    assert "session=" in result
    assert "failed" in result


def test_whitespace_collapsed():
    result = normalize_error("error  \n  message")
    assert result == "error message"


# ---------------------------------------------------------------------------
# compute_signature — hash properties
# ---------------------------------------------------------------------------


def test_empty_string_produces_hash():
    result = compute_signature("")
    assert isinstance(result, str)
    assert len(result) == 64


def test_compute_signature_deterministic():
    input_text = "AssertionError: expected True, got False"
    assert compute_signature(input_text) == compute_signature(input_text)


def test_compute_signature_different_inputs():
    hash1 = compute_signature("AssertionError: expected True, got False")
    hash2 = compute_signature("ConnectionError: timeout after 30s")
    assert hash1 != hash2


@pytest.mark.parametrize(
    "raw",
    [
        "simple error message",
        "\x1b[31mERROR\x1b[0m: something went wrong",
        "2024-01-15T10:30:00Z FAILED at 0x7fff1234 line 99",
        "session=550e8400-e29b-41d4-a716-446655440000 crashed",
        "",
    ],
)
def test_signature_length_always_64(raw: str):
    result = compute_signature(raw)
    assert len(result) == 64


# ---------------------------------------------------------------------------
# log_analyzer_node — async node tests using real test DB
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure and return the TestFailure."""
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id="run-1",
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
        test_name="test_example",
        error_message="AssertionError: expected True, got False",
        stack_trace="File test.py, line 42\nAssertionError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


async def test_log_analyzer_sets_normalized_error_text_in_state(
    db_session: AsyncSession,
):
    """log_analyzer_node returns a dict with 'normalized_error_text' as a non-empty string."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    session_factory = _make_session_factory(db_session)

    with patch(
        "src.agents.nodes.log_analyzer.get_session_factory",
        return_value=session_factory,
    ):
        result = await log_analyzer_node(state)

    assert "normalized_error_text" in result
    assert isinstance(result["normalized_error_text"], str)
    assert len(result["normalized_error_text"]) > 0


async def test_log_analyzer_normalized_error_text_differs_from_hash(
    db_session: AsyncSession,
):
    """normalized_error_text is the human-readable normalized string, not the hex digest.

    The hash is always a 64-character hex string; the normalized text will
    contain words and spaces from the original error message.
    """
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    session_factory = _make_session_factory(db_session)

    with patch(
        "src.agents.nodes.log_analyzer.get_session_factory",
        return_value=session_factory,
    ):
        result = await log_analyzer_node(state)

    normalized_text = result["normalized_error_text"]
    error_signature = result["error_signature"]

    # The hash is a 64-char lowercase hex string; the normalized text is not.
    assert normalized_text != error_signature
    assert len(error_signature) == 64
    assert " " in normalized_text or len(normalized_text) != 64
