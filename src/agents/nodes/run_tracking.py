from __future__ import annotations

import uuid

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.config.constants import AgentRunStatus
from src.db.repositories.agent_run_repo import AgentRunRepository

logger = structlog.get_logger(__name__)

_MAX_SUMMARY_LENGTH = 1000


def truncate_summary(text: str | None, max_length: int = _MAX_SUMMARY_LENGTH) -> str | None:
    """Trim a summary string to a reasonable length for storage/display.

    Returns ``None`` unchanged. Strips surrounding whitespace and appends an
    ellipsis when truncation occurs.
    """
    if text is None:
        return None
    text = text.strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


async def start_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    test_failure_id: uuid.UUID,
    agent_name: str,
    input_summary: str | None = None,
) -> uuid.UUID | None:
    """Create a ``running`` AgentRun row in its own committed transaction.

    Using a dedicated session/transaction (rather than the caller's main
    session) means the AgentRun row survives even if the caller's main
    transaction later rolls back due to an error.

    Returns the new row's id, or ``None`` if the insert failed. Best-effort:
    errors are logged and swallowed so instrumentation never breaks the
    triage pipeline.
    """
    repo = AgentRunRepository()
    try:
        async with session_factory() as session:
            run = await repo.create(
                session,
                test_failure_id=test_failure_id,
                agent_name=agent_name,
                input_summary=truncate_summary(input_summary),
            )
            await session.commit()
            return run.id
    except Exception as exc:
        logger.warning(
            "agent_run.start_failed",
            agent_name=agent_name,
            test_failure_id=str(test_failure_id),
            error=str(exc),
        )
        return None


async def finish_agent_run(
    session_factory: async_sessionmaker[AsyncSession],
    run_id: uuid.UUID | None,
    *,
    status: str,
    output_summary: str | None = None,
    tokens_used: int | None = None,
) -> None:
    """Mark an AgentRun row as completed/failed/skipped in its own transaction.

    No-op if ``run_id`` is ``None`` (e.g. because :func:`start_agent_run`
    failed earlier). Best-effort: errors are logged and swallowed.
    """
    if run_id is None:
        return
    repo = AgentRunRepository()
    try:
        async with session_factory() as session:
            await repo.complete(
                session,
                run_id,
                status=status,
                output_summary=truncate_summary(output_summary),
                tokens_used=tokens_used,
            )
            await session.commit()
    except Exception as exc:
        logger.warning(
            "agent_run.finish_failed",
            agent_run_id=str(run_id),
            status=status,
            error=str(exc),
        )


async def record_agent_runs(
    session_factory: async_sessionmaker[AsyncSession],
    failure_ids: list[str],
    *,
    agent_name: str,
    status: str = AgentRunStatus.SKIPPED,
    input_summary: str | None = None,
    output_summary: str | None = None,
) -> None:
    """Record an immediately-completed AgentRun for each id in ``failure_ids``.

    Used for whole-node short-circuit paths where no per-failure timing makes
    sense — e.g. heal_suggester bailing out because classification confidence
    is too low, or visual_analyzer skipping because no screenshots exist.
    ``status`` is typically :class:`AgentRunStatus.SKIPPED`. Best-effort:
    failures to write a given row are logged and swallowed.
    """
    repo = AgentRunRepository()
    summary_in = truncate_summary(input_summary)
    summary_out = truncate_summary(output_summary)

    for failure_id in failure_ids:
        try:
            async with session_factory() as session:
                run = await repo.create(
                    session,
                    test_failure_id=uuid.UUID(failure_id),
                    agent_name=agent_name,
                    input_summary=summary_in,
                )
                await repo.complete(
                    session,
                    run.id,
                    status=status,
                    output_summary=summary_out,
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "agent_run.record_failed",
                agent_name=agent_name,
                failure_id=failure_id,
                error=str(exc),
            )
