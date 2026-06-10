"""Unit tests for src/services/triage_service.py.

Covers:
  - run_triage returns the final TriageState as a plain dict with expected keys
  - run_triage logs the pipeline_event_id at completion
  - run_triage propagates exceptions raised by triage_graph.ainvoke
  - run_triage handles is_duplicate=True result and logs it correctly
  - run_triage marks each failure_id in the result as "triaged"

triage_graph.ainvoke, _update_pipeline_status, and _mark_failures_triaged are
patched for all tests — no real LangGraph execution or database access occurs.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.state import initial_state
from src.services.triage_service import run_triage

# Patch _update_pipeline_status across all tests so no DB connection is attempted.
# The function is tested separately; unit tests here focus solely on the graph
# invocation, return value, and log output.
_PATCH_DB_UPDATE = patch(
    "src.services.triage_service._update_pipeline_status",
    new=AsyncMock(return_value=None),
)

# Patch _mark_failures_triaged across all tests so no DB connection is attempted.
# The function is tested separately (see test_run_triage_marks_failures_triaged).
_PATCH_MARK_TRIAGED = patch(
    "src.services.triage_service._mark_failures_triaged",
    new=AsyncMock(return_value=None),
)

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

    with _PATCH_DB_UPDATE, _PATCH_MARK_TRIAGED, patch(
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


async def test_run_triage_logs_completion(
    capfd: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run_triage emits a triage_service.completed log event containing pipeline_event_id.

    structlog may route output via ConsoleRenderer (→ stdout) in local dev or via
    the stdlib logging bridge (→ caplog) in CI, depending on how the test session
    is configured.  We check both to remain environment-agnostic:

    * capfd captures raw file-descriptor output regardless of which Python stream
      object is written to (works when structlog uses ConsoleRenderer).
    * caplog captures stdlib-logger records (works when structlog uses the stdlib
      bridge that some integration-test fixtures activate).
    """
    final_state = _make_final_state(
        {"failure_ids": ["cccc-3333"], "is_duplicate": False, "errors": []}
    )

    with _PATCH_DB_UPDATE, _PATCH_MARK_TRIAGED, patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(return_value=final_state),
    ):
        await run_triage(_PIPELINE_EVENT_ID)

    # Combine both capture sources; at least one will contain the structlog output.
    combined = capfd.readouterr().out + caplog.text
    assert _PIPELINE_EVENT_ID in combined
    assert "triage_service.completed" in combined
    assert "cccc-3333" in combined
    assert "is_duplicate" in combined


# ===========================================================================
# Test 3 — run_triage propagates exceptions from triage_graph.ainvoke
# ===========================================================================


async def test_run_triage_propagates_graph_errors():
    """run_triage does not swallow exceptions raised by triage_graph.ainvoke."""
    with _PATCH_DB_UPDATE, _PATCH_MARK_TRIAGED, patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(side_effect=RuntimeError("graph execution failed")),
    ), pytest.raises(RuntimeError, match="graph execution failed"):
        await run_triage(_PIPELINE_EVENT_ID)


# ===========================================================================
# Test 4 — run_triage handles is_duplicate=True and logs it correctly
# ===========================================================================


async def test_run_triage_handles_duplicate_result(
    capfd: pytest.CaptureFixture,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """run_triage logs is_duplicate=True when the graph detects a duplicate failure."""
    duplicate_state = _make_final_state(
        {
            "is_duplicate": True,
            "duplicate_of_id": "existing-failure-uuid",
            "failure_ids": ["dddd-4444"],
            "errors": [],
        }
    )

    with _PATCH_DB_UPDATE, _PATCH_MARK_TRIAGED, patch(
        "src.services.triage_service.triage_graph",
        ainvoke=AsyncMock(return_value=duplicate_state),
    ):
        result = await run_triage(_PIPELINE_EVENT_ID)

    assert result["is_duplicate"] is True
    assert result["duplicate_of_id"] == "existing-failure-uuid"

    # Check both capture sources for the same reason as test_run_triage_logs_completion.
    combined = capfd.readouterr().out + caplog.text
    assert "triage_service.completed" in combined
    assert "is_duplicate" in combined


# ===========================================================================
# Test 5 — run_triage marks each failure_id in the result as "triaged"
# ===========================================================================


async def test_run_triage_marks_failures_triaged():
    """run_triage's _mark_failures_triaged helper updates each failure_id in
    result["failure_ids"] to status="triaged" via FailureRepository.update_status,
    mirroring how _update_pipeline_status updates the pipeline event.

    _mark_failures_triaged is NOT patched here (unlike the other tests) so we
    can verify its DB-facing behavior; instead FailureRepository and the
    session factory are mocked, matching the existing mocking style for
    _update_pipeline_status's own dedicated tests.
    """
    failure_id_a = str(uuid.uuid4())
    failure_id_b = str(uuid.uuid4())

    final_state = _make_final_state(
        {"failure_ids": [failure_id_a, failure_id_b], "is_duplicate": False, "errors": []}
    )

    mock_session = AsyncMock()
    mock_failure_repo = MagicMock()
    mock_failure_repo.update_status = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    def _session_factory():
        return _ctx()

    with (
        _PATCH_DB_UPDATE,
        patch(
            "src.services.triage_service.triage_graph",
            ainvoke=AsyncMock(return_value=final_state),
        ),
        patch(
            "src.services.triage_service.get_session_factory",
            return_value=_session_factory,
        ),
        patch(
            "src.services.triage_service.FailureRepository",
            return_value=mock_failure_repo,
        ),
    ):
        result = await run_triage(_PIPELINE_EVENT_ID)

    assert result["failure_ids"] == [failure_id_a, failure_id_b]
    assert mock_failure_repo.update_status.await_count == 2

    updated_ids = {
        str(call.args[1]) for call in mock_failure_repo.update_status.call_args_list
    }
    assert updated_ids == {failure_id_a, failure_id_b}

    for call in mock_failure_repo.update_status.call_args_list:
        assert call.args[2] == "triaged"
