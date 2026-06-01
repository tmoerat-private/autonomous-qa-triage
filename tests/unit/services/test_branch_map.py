"""Unit tests for get_branch_map() in failure_service.

Covers:
  - Correct branch mapping when multiple PipelineEvents are queried
  - Empty list fast-path returning {}
  - NULL branch column mapping to None

All tests hit a real PostgreSQL test database with transaction rollback
(see tests/conftest.py).  No external services are called.
"""
from __future__ import annotations

import uuid

from src.models.pipeline_event import PipelineEvent
from src.services.failure_service import get_branch_map

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _make_event(
    db,
    branch: str | None = "main",
) -> PipelineEvent:
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id=f"run-{uuid.uuid4().hex[:8]}",
        repository="org/repo",
        branch=branch,
        commit_sha=uuid.uuid4().hex[:40],
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db.add(event)
    await db.flush()
    return event


# ===========================================================================
# Test A — get_branch_map returns correct branch values for multiple events
# ===========================================================================


async def test_get_branch_map_returns_correct_branch(db_session):
    """get_branch_map returns {event_id: branch} with the correct branch for each event."""
    event1 = await _make_event(db_session, branch="main")
    event2 = await _make_event(db_session, branch="feature/x")

    result = await get_branch_map(db_session, [event1.id, event2.id])

    assert event1.id in result
    assert event2.id in result
    assert result[event1.id] == "main"
    assert result[event2.id] == "feature/x"


# ===========================================================================
# Test B — get_branch_map with an empty list returns {}
# ===========================================================================


async def test_get_branch_map_empty_list(db_session):
    """get_branch_map returns an empty dict when given an empty pipeline_event_ids list."""
    result = await get_branch_map(db_session, [])

    assert result == {}


# ===========================================================================
# Test C — get_branch_map maps to None when branch column is NULL
# ===========================================================================


async def test_get_branch_map_none_branch(db_session):
    """get_branch_map maps event.id → None when PipelineEvent.branch is NULL."""
    event = await _make_event(db_session, branch=None)

    result = await get_branch_map(db_session, [event.id])

    assert event.id in result
    assert result[event.id] is None
