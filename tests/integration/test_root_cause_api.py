"""Integration tests for the root-cause API endpoint and list-failures branch field.

Covers:
  - GET /api/v1/failures/{id}/root-cause returns 200 with correct body fields
  - GET /api/v1/failures/{id}/root-cause returns 404 when no analysis exists
  - GET /api/v1/failures/{id}/root-cause returns 404 for an unknown failure ID
  - GET /api/v1/failures list items carry the branch field from PipelineEvent

All tests hit a real PostgreSQL test database via the shared `client` and
`db_session` fixtures defined in tests/conftest.py.  Celery is mocked so no
real task dispatch occurs.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.models.pipeline_event import PipelineEvent
from src.models.root_cause_analysis import RootCauseAnalysis
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Module-level autouse — no real Celery calls in any test in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_celery_task():
    """Replace run_triage_pipeline.delay with a no-op for every test."""
    with patch("src.api.routes.failures.run_triage_pipeline") as mock_task:
        mock_task.delay = MagicMock(return_value=None)
        yield mock_task


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _make_event(
    db,
    branch: str | None = "main",
    repository: str | None = "org/repo",
) -> PipelineEvent:
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id=f"run-{uuid.uuid4().hex[:8]}",
        repository=repository,
        branch=branch,
        commit_sha=uuid.uuid4().hex[:40],
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db.add(event)
    await db.flush()
    return event


async def _make_failure(db, event: PipelineEvent) -> TestFailure:
    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name=f"test_feature_{uuid.uuid4().hex[:6]}",
        error_message="AssertionError: expected True, got False",
        stack_trace="Traceback (most recent call last):\n  File 'test_foo.py', line 1\nAssertionError",
        status="new",
    )
    db.add(failure)
    await db.flush()
    return failure


async def _make_root_cause(
    db,
    failure: TestFailure,
    event: PipelineEvent,
    category: str = "code_regression",
) -> RootCauseAnalysis:
    analysis = RootCauseAnalysis(
        test_failure_id=failure.id,
        pipeline_event_id=event.id,
        root_cause_summary="A recent code change introduced a regression.",
        root_cause_category=category,
        likely_cause_files=["src/services/auth.py"],
        investigation_steps=["Check git log", "Run tests locally"],
        model_used="claude-sonnet-4-6",
    )
    db.add(analysis)
    await db.flush()
    return analysis


# ===========================================================================
# Test G — 200 with correct body when a RootCauseAnalysis exists
# ===========================================================================


async def test_get_failure_root_cause_returns_200(client, db_session):
    """GET /api/v1/failures/{id}/root-cause returns 200 with all expected fields."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)
    await _make_root_cause(db_session, failure, event)

    response = await client.get(f"/api/v1/failures/{failure.id}/root-cause")

    assert response.status_code == 200
    body = response.json()
    assert body["root_cause_summary"] == "A recent code change introduced a regression."
    assert body["root_cause_category"] == "code_regression"
    assert body["likely_cause_files"] == ["src/services/auth.py"]
    assert body["investigation_steps"] == ["Check git log", "Run tests locally"]


# ===========================================================================
# Test H — 404 when failure exists but has no RootCauseAnalysis
# ===========================================================================


async def test_get_failure_root_cause_returns_404_when_no_analysis(client, db_session):
    """GET /api/v1/failures/{id}/root-cause returns 404 when no analysis has been persisted."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)
    # No RootCauseAnalysis created

    response = await client.get(f"/api/v1/failures/{failure.id}/root-cause")

    assert response.status_code == 404


# ===========================================================================
# Test I — 404 for a completely unknown failure UUID
# ===========================================================================


async def test_get_failure_root_cause_returns_404_for_unknown_failure(client, db_session):
    """GET /api/v1/failures/{random_uuid}/root-cause returns 404 for a non-existent failure."""
    unknown_id = uuid.uuid4()

    response = await client.get(f"/api/v1/failures/{unknown_id}/root-cause")

    assert response.status_code == 404


# ===========================================================================
# Test J (bonus) — list failures includes branch from PipelineEvent
# ===========================================================================


async def test_list_failures_includes_branch(client, db_session):
    """GET /api/v1/failures list items carry branch stamped from the linked PipelineEvent."""
    event = await _make_event(db_session, branch="feature/my-branch")
    failure = await _make_failure(db_session, event)

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    items = response.json()["items"]
    target = next((i for i in items if i["id"] == str(failure.id)), None)
    assert target is not None, "seeded failure not found in list response"
    assert target["branch"] == "feature/my-branch"
