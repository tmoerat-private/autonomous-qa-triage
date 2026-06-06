"""Unit tests for src/services/triage_service.py.

Covers:
  - run_triage returns the final TriageState as a plain dict with expected keys
  - run_triage logs the pipeline_event_id at completion
  - run_triage propagates exceptions raised by triage_graph.ainvoke
  - run_triage handles is_duplicate=True result and logs it correctly

triage_graph.ainvoke is patched with AsyncMock for all tests — no real LangGraph
execution or database access occurs.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, patch

import pytest

from src.agents.state import initial_state
from src.services.triage_service import run_triage

# ---------------------------------------------------------------------------
# Shared test data
# ---------------------------------------------------------------------------

_PIPELINE_EVENT_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_final_state(overrides: dict | None = None) -> dict:
    """Return a realistic final TriageState dict, optionally modified by overrides."""
    state = dict(initial_state(_PIPELINE_EVENT_ID))
    state.update(
        {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "failure_ids": ["aaaa-1111", "bbbb-2222"],
            "is_duplicate": False,
            "errors": [],
        }
    )
    if overrides:
        state.update(overrides)
    return state


# ===========================================================================
# Test 1 — run_triage returns the final state as a plain dict
# ===========================================================================


async def test_run_triage_returns_final_state():
    """run_triage returns the full final TriageState dict from triage_graph.ainvoke."""
    expected = _make_final_state()

    with patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(return_value=expected),
    ):
        result = await run_triage(_PIPELINE_EVENT_ID)

    assert isinstance(result, dict)
    assert result["pipeline_event_id"] == _PIPELINE_EVENT_ID
    assert result["failure_ids"] == ["aaaa-1111", "bbbb-2222"]
    assert result["is_duplicate"] is False
    assert result["errors"] == []


# ===========================================================================
# Test 2 — run_triage logs the pipeline_event_id at completion
# ===========================================================================


async def test_run_triage_logs_completion(caplog: pytest.LogCaptureFixture) -> None:
    """run_triage emits a triage_service.completed log event containing pipeline_event_id.

    structlog is configured with cache_logger_on_first_use=True and stdlib as the backend
    (BoundLogger + ProcessorFormatter), so structlog.testing.capture_logs() cannot intercept
    already-cached loggers.  pytest's caplog fixture captures the stdlib output instead.
    """
    final_state = _make_final_state(
        {"failure_ids": ["cccc-3333"], "is_duplicate": False, "errors": []}
    )

    with patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(return_value=final_state),
    ), caplog.at_level(logging.INFO):
        await run_triage(_PIPELINE_EVENT_ID)

    assert _PIPELINE_EVENT_ID in caplog.text
    assert "triage_service.completed" in caplog.text
    assert "cccc-3333" in caplog.text
    assert "is_duplicate" in caplog.text


# ===========================================================================
# Test 3 — run_triage propagates exceptions from triage_graph.ainvoke
# ===========================================================================


async def test_run_triage_propagates_graph_errors():
    """run_triage does not swallow exceptions raised by triage_graph.ainvoke."""
    with patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(side_effect=RuntimeError("graph execution failed")),
    ), pytest.raises(RuntimeError, match="graph execution failed"):
        await run_triage(_PIPELINE_EVENT_ID)


# ===========================================================================
# Test 4 — run_triage handles is_duplicate=True and logs it correctly
# ===========================================================================


async def test_run_triage_handles_duplicate_result(caplog: pytest.LogCaptureFixture) -> None:
    """run_triage logs is_duplicate=True when the graph detects a duplicate failure."""
    duplicate_state = _make_final_state(
        {
            "is_duplicate": True,
            "duplicate_of_id": "existing-failure-uuid",
            "failure_ids": ["dddd-4444"],
            "errors": [],
        }
    )

    with patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(return_value=duplicate_state),
    ), caplog.at_level(logging.INFO):
        result = await run_triage(_PIPELINE_EVENT_ID)

    assert result["is_duplicate"] is True
    assert result["duplicate_of_id"] == "existing-failure-uuid"

    assert "triage_service.completed" in caplog.text
    assert "is_duplicate" in caplog.text
