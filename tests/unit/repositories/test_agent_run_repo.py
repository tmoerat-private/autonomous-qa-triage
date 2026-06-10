"""Unit tests for AgentRunRepository.

Covers:
  - create() persists a RUNNING AgentRun row with started_at set
  - complete() updates status, output_summary, tokens_used, completed_at, duration_ms
  - complete() leaves output_summary unchanged when not provided
  - complete() returns None for an unknown run_id

All tests hit a real PostgreSQL test database with transaction rollback
(see tests/conftest.py). No external services are called.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from src.config.constants import AgentRunStatus
from src.db.repositories.agent_run_repo import AgentRunRepository
from src.models.pipeline_event import PipelineEvent
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


# ===========================================================================
# create()
# ===========================================================================


async def test_create_persists_running_agent_run(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session,
        test_failure_id=failure.id,
        agent_name="failure_classifier",
        input_summary="Test: test_feature\nError: AssertionError",
    )

    assert run.id is not None
    assert run.test_failure_id == failure.id
    assert run.agent_name == "failure_classifier"
    assert run.status == AgentRunStatus.RUNNING
    assert run.input_summary == "Test: test_feature\nError: AssertionError"
    assert run.started_at is not None
    assert run.completed_at is None
    assert run.duration_ms is None


async def test_create_default_status_is_running(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session, test_failure_id=failure.id, agent_name="log_analyzer"
    )

    assert run.status == AgentRunStatus.RUNNING
    assert run.input_summary is None


# ===========================================================================
# complete()
# ===========================================================================


async def test_complete_updates_status_output_and_tokens(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session, test_failure_id=failure.id, agent_name="ticket_creator"
    )

    completed = await repo.complete(
        db_session,
        run.id,
        status=AgentRunStatus.COMPLETED,
        output_summary="Created Jira ticket QA-1",
        tokens_used=512,
    )

    assert completed is not None
    assert completed.id == run.id
    assert completed.status == AgentRunStatus.COMPLETED
    assert completed.output_summary == "Created Jira ticket QA-1"
    assert completed.tokens_used == 512
    assert completed.completed_at is not None
    assert completed.duration_ms is not None
    assert completed.duration_ms >= 0


async def test_complete_computes_duration_from_started_at(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session, test_failure_id=failure.id, agent_name="root_cause"
    )

    # Backdate started_at so duration_ms is deterministically large.
    run.started_at = datetime.now(UTC) - timedelta(milliseconds=500)
    await db_session.flush()

    completed = await repo.complete(db_session, run.id, status=AgentRunStatus.COMPLETED)

    assert completed is not None
    assert completed.duration_ms is not None
    assert completed.duration_ms >= 500


async def test_complete_without_output_summary_leaves_existing_value(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session,
        test_failure_id=failure.id,
        agent_name="notifier",
        input_summary="initial",
    )
    run.output_summary = "pre-existing summary"
    await db_session.flush()

    completed = await repo.complete(db_session, run.id, status=AgentRunStatus.SKIPPED)

    assert completed is not None
    assert completed.status == AgentRunStatus.SKIPPED
    assert completed.output_summary == "pre-existing summary"


async def test_complete_without_tokens_used_leaves_it_none(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session, test_failure_id=failure.id, agent_name="learner"
    )

    completed = await repo.complete(
        db_session,
        run.id,
        status=AgentRunStatus.COMPLETED,
        output_summary="Stored outcome embedding",
    )

    assert completed is not None
    assert completed.tokens_used is None


async def test_complete_failed_status_with_output_summary(db_session):
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)

    repo = AgentRunRepository()
    run = await repo.create(
        db_session, test_failure_id=failure.id, agent_name="learner"
    )

    completed = await repo.complete(
        db_session,
        run.id,
        status=AgentRunStatus.FAILED,
        output_summary="ConnectionError: could not reach Qdrant",
    )

    assert completed is not None
    assert completed.status == AgentRunStatus.FAILED
    assert completed.output_summary == "ConnectionError: could not reach Qdrant"


async def test_complete_returns_none_for_unknown_run_id(db_session):
    repo = AgentRunRepository()
    result = await repo.complete(db_session, uuid.uuid4(), status=AgentRunStatus.FAILED)
    assert result is None
