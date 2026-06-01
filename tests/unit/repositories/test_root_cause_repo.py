"""Unit tests for RootCauseRepository.

Covers:
  - create() + get_latest_by_failure_id() returns the created record
  - get_by_failure_id() returns multiple rows ordered DESC by created_at
  - get_latest_by_failure_id() returns None when no rows exist for that failure

All tests hit a real PostgreSQL test database with transaction rollback
(see tests/conftest.py).  No external services are called.
"""
from __future__ import annotations

import asyncio
import uuid

from src.db.repositories.root_cause_repo import RootCauseRepository
from src.models.pipeline_event import PipelineEvent
from src.models.root_cause_analysis import RootCauseAnalysis
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


async def _make_event(db) -> PipelineEvent:
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id=f"run-{uuid.uuid4().hex[:8]}",
        repository="org/repo",
        branch="main",
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
    summary: str = "A recent code change introduced a regression.",
) -> RootCauseAnalysis:
    analysis = RootCauseAnalysis(
        test_failure_id=failure.id,
        pipeline_event_id=event.id,
        root_cause_summary=summary,
        root_cause_category=category,
        likely_cause_files=["src/services/auth.py"],
        investigation_steps=["Check git log", "Run tests locally"],
        model_used="claude-sonnet-4-6",
    )
    db.add(analysis)
    await db.flush()
    return analysis


# ===========================================================================
# Test D — create() and get_latest_by_failure_id() round-trip
# ===========================================================================


async def test_root_cause_repo_create_and_get_latest(db_session):
    """RootCauseRepository.create() persists a record; get_latest_by_failure_id() retrieves it."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = RootCauseRepository()
    created = await repo.create(
        db_session,
        test_failure_id=failure.id,
        pipeline_event_id=event.id,
        root_cause_summary="A recent code change introduced a regression.",
        root_cause_category="code_regression",
        likely_cause_files=["src/foo.py"],
        investigation_steps=["Check git log"],
        model_used="claude-sonnet-4-6",
    )

    latest = await repo.get_latest_by_failure_id(db_session, failure.id)

    assert latest is not None
    assert latest.id == created.id
    assert latest.test_failure_id == failure.id
    assert latest.pipeline_event_id == event.id
    assert latest.root_cause_summary == "A recent code change introduced a regression."
    assert latest.root_cause_category == "code_regression"
    assert latest.likely_cause_files == ["src/foo.py"]
    assert latest.investigation_steps == ["Check git log"]
    assert latest.model_used == "claude-sonnet-4-6"


# ===========================================================================
# Test E — get_by_failure_id() returns all rows ordered DESC by created_at
# ===========================================================================


async def test_root_cause_repo_get_by_failure_id_multiple(db_session):
    """get_by_failure_id() returns a list of all analyses ordered newest-first."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    first = await _make_root_cause(
        db_session, failure, event, summary="First analysis"
    )
    # Small sleep so the DB-generated created_at timestamps are distinct
    await asyncio.sleep(0.01)
    second = await _make_root_cause(
        db_session, failure, event, summary="Second analysis"
    )

    repo = RootCauseRepository()
    results = await repo.get_by_failure_id(db_session, failure.id)

    assert len(results) == 2
    # Ordered DESC by created_at — newest row comes first
    assert results[0].id == second.id
    assert results[1].id == first.id


# ===========================================================================
# Test F — get_latest_by_failure_id() returns None when no analyses exist
# ===========================================================================


async def test_root_cause_repo_get_latest_returns_none_when_empty(db_session):
    """get_latest_by_failure_id() returns None when no RootCauseAnalysis rows exist for that ID."""
    unknown_id = uuid.uuid4()

    repo = RootCauseRepository()
    result = await repo.get_latest_by_failure_id(db_session, unknown_id)

    assert result is None
