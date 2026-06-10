"""Unit tests for src.agents.nodes.run_tracking helpers.

Covers:
  - truncate_summary(): None passthrough, short text unchanged, long text
    truncated with an ellipsis, custom max_length.
  - start_agent_run(): creates a RUNNING AgentRun row and returns its id;
    swallows errors and returns None on failure.
  - finish_agent_run(): no-op when run_id is None; updates status/duration/
    output_summary/tokens_used on success; swallows errors.
  - record_agent_runs(): creates an immediately-completed AgentRun row per
    failure id; swallows per-row errors (invalid UUIDs, FK violations) without
    raising and without blocking other rows.

These tests use a dedicated session_factory built directly on the `engine`
fixture (not `db_session`) because run_tracking helpers open their own
sessions and call session.commit(). All writes are cleaned up by the
`engine` fixture's table drop_all teardown (see tests/conftest.py).
"""
from __future__ import annotations

import uuid

import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.agents.nodes.run_tracking import (
    finish_agent_run,
    record_agent_runs,
    start_agent_run,
    truncate_summary,
)
from src.config.constants import AgentRunStatus
from src.models.agent_run import AgentRun
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """A real session factory backed by the per-test `engine`.

    Unlike `db_session`, sessions created by this factory commit for real —
    cleanup happens via `engine`'s drop_all teardown, not transaction rollback.
    """
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def seeded_failure_id(session_factory) -> uuid.UUID:
    """Persist a PipelineEvent + TestFailure and return the failure's id.

    AgentRun.test_failure_id is a NOT NULL FK to test_failures.id, so any row
    created by run_tracking helpers needs a real TestFailure to point at.
    """
    async with session_factory() as session:
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
        session.add(event)
        await session.flush()

        failure = TestFailure(
            pipeline_event_id=event.id,
            test_name=f"test_feature_{uuid.uuid4().hex[:6]}",
            error_message="AssertionError: expected True, got False",
            stack_trace="Traceback...",
            status="new",
        )
        session.add(failure)
        await session.flush()
        await session.commit()
        return failure.id


# ===========================================================================
# truncate_summary
# ===========================================================================


def test_truncate_summary_none_passthrough():
    assert truncate_summary(None) is None


def test_truncate_summary_short_text_unchanged():
    assert truncate_summary("short summary") == "short summary"


def test_truncate_summary_strips_surrounding_whitespace():
    assert truncate_summary("  short summary  ") == "short summary"


def test_truncate_summary_truncates_long_text_with_ellipsis():
    text = "x" * 2000
    result = truncate_summary(text)

    assert result is not None
    assert len(result) == 1000
    assert result.endswith("…")
    assert result[:-1] == "x" * 999


def test_truncate_summary_respects_custom_max_length():
    text = "abcdefghij"
    result = truncate_summary(text, max_length=5)

    assert result == "abcd…"


def test_truncate_summary_text_at_exact_limit_unchanged():
    text = "x" * 1000
    assert truncate_summary(text) == text


# ===========================================================================
# start_agent_run
# ===========================================================================


async def test_start_agent_run_creates_running_row(session_factory, seeded_failure_id):
    run_id = await start_agent_run(
        session_factory,
        test_failure_id=seeded_failure_id,
        agent_name="failure_classifier",
        input_summary="Test: test_feature\nError: boom",
    )

    assert run_id is not None

    async with session_factory() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one()

    assert run.test_failure_id == seeded_failure_id
    assert run.agent_name == "failure_classifier"
    assert run.status == AgentRunStatus.RUNNING
    assert run.input_summary == "Test: test_feature\nError: boom"
    assert run.completed_at is None


async def test_start_agent_run_truncates_input_summary(session_factory, seeded_failure_id):
    long_summary = "y" * 2000

    run_id = await start_agent_run(
        session_factory,
        test_failure_id=seeded_failure_id,
        agent_name="log_analyzer",
        input_summary=long_summary,
    )

    async with session_factory() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one()

    assert run.input_summary is not None
    assert len(run.input_summary) == 1000
    assert run.input_summary.endswith("…")


async def test_start_agent_run_swallows_errors_and_returns_none(session_factory):
    """An invalid test_failure_id (no matching test_failures row) violates the
    FK constraint on insert; start_agent_run must catch this and return None
    rather than raising."""
    run_id = await start_agent_run(
        session_factory,
        test_failure_id=uuid.uuid4(),
        agent_name="failure_classifier",
        input_summary="orphaned run",
    )

    assert run_id is None


# ===========================================================================
# finish_agent_run
# ===========================================================================


async def test_finish_agent_run_noop_when_run_id_none(session_factory):
    # Should return without error and without touching the database.
    await finish_agent_run(
        session_factory,
        None,
        status=AgentRunStatus.COMPLETED,
        output_summary="should not be persisted",
    )


async def test_finish_agent_run_updates_status_and_output(session_factory, seeded_failure_id):
    run_id = await start_agent_run(
        session_factory,
        test_failure_id=seeded_failure_id,
        agent_name="ticket_creator",
        input_summary="Test: test_feature",
    )
    assert run_id is not None

    await finish_agent_run(
        session_factory,
        run_id,
        status=AgentRunStatus.COMPLETED,
        output_summary="Created Jira ticket QA-1",
        tokens_used=256,
    )

    async with session_factory() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one()

    assert run.status == AgentRunStatus.COMPLETED
    assert run.output_summary == "Created Jira ticket QA-1"
    assert run.tokens_used == 256
    assert run.completed_at is not None
    assert run.duration_ms is not None


async def test_finish_agent_run_truncates_output_summary(session_factory, seeded_failure_id):
    run_id = await start_agent_run(
        session_factory,
        test_failure_id=seeded_failure_id,
        agent_name="notifier",
    )
    assert run_id is not None

    await finish_agent_run(
        session_factory,
        run_id,
        status=AgentRunStatus.FAILED,
        output_summary="z" * 2000,
    )

    async with session_factory() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one()

    assert run.status == AgentRunStatus.FAILED
    assert run.output_summary is not None
    assert len(run.output_summary) == 1000
    assert run.output_summary.endswith("…")


async def test_finish_agent_run_swallows_session_factory_errors():
    """If session_factory() itself raises, finish_agent_run must swallow it."""

    def _broken_factory():
        raise RuntimeError("boom")

    # Should not raise.
    await finish_agent_run(
        _broken_factory,  # type: ignore[arg-type]
        uuid.uuid4(),
        status=AgentRunStatus.FAILED,
        output_summary="irrelevant",
    )


# ===========================================================================
# record_agent_runs
# ===========================================================================


async def test_record_agent_runs_creates_completed_row_per_failure(
    session_factory, seeded_failure_id
):
    await record_agent_runs(
        session_factory,
        [str(seeded_failure_id)],
        agent_name="heal_suggester",
        status=AgentRunStatus.SKIPPED,
        output_summary="Skipped: confidence below threshold",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(AgentRun).where(
                AgentRun.test_failure_id == seeded_failure_id,
                AgentRun.agent_name == "heal_suggester",
            )
        )
        runs = result.scalars().all()

    assert len(runs) == 1
    run = runs[0]
    assert run.status == AgentRunStatus.SKIPPED
    assert run.output_summary == "Skipped: confidence below threshold"
    assert run.completed_at is not None
    assert run.duration_ms is not None


async def test_record_agent_runs_default_status_is_skipped(session_factory, seeded_failure_id):
    await record_agent_runs(
        session_factory,
        [str(seeded_failure_id)],
        agent_name="visual_analyzer",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(AgentRun).where(
                AgentRun.test_failure_id == seeded_failure_id,
                AgentRun.agent_name == "visual_analyzer",
            )
        )
        run = result.scalar_one()

    assert run.status == AgentRunStatus.SKIPPED


async def test_record_agent_runs_swallows_invalid_uuid_and_continues(
    session_factory, seeded_failure_id
):
    """A non-UUID failure_id must not raise and must not block valid ids."""
    await record_agent_runs(
        session_factory,
        ["not-a-valid-uuid", str(seeded_failure_id)],
        agent_name="duplicate_detector",
        status=AgentRunStatus.COMPLETED,
        output_summary="No duplicates found",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(AgentRun).where(
                AgentRun.test_failure_id == seeded_failure_id,
                AgentRun.agent_name == "duplicate_detector",
            )
        )
        run = result.scalar_one()

    assert run.status == AgentRunStatus.COMPLETED
    assert run.output_summary == "No duplicates found"


async def test_record_agent_runs_swallows_fk_violation_and_continues(
    session_factory, seeded_failure_id
):
    """A failure_id with no matching test_failures row violates the FK and is
    swallowed; subsequent valid ids are still processed."""
    orphan_id = str(uuid.uuid4())

    await record_agent_runs(
        session_factory,
        [orphan_id, str(seeded_failure_id)],
        agent_name="release_scorer",
        status=AgentRunStatus.COMPLETED,
        output_summary="score=10/100 (low)",
    )

    async with session_factory() as session:
        result = await session.execute(
            select(AgentRun).where(AgentRun.agent_name == "release_scorer")
        )
        runs = result.scalars().all()

    assert len(runs) == 1
    assert runs[0].test_failure_id == seeded_failure_id
    assert runs[0].status == AgentRunStatus.COMPLETED


async def test_record_agent_runs_empty_failure_ids_is_noop(session_factory):
    # Should not raise even with an empty list.
    await record_agent_runs(
        session_factory,
        [],
        agent_name="release_scorer",
        status=AgentRunStatus.SKIPPED,
    )
