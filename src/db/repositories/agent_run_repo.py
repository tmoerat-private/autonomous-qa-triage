from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import AgentRunStatus
from src.models.agent_run import AgentRun

logger = structlog.get_logger(__name__)


class AgentRunRepository:
    """Data access layer for AgentRun records."""

    async def create(
        self,
        session: AsyncSession,
        *,
        test_failure_id: UUID,
        agent_name: str,
        status: str = AgentRunStatus.RUNNING,
        input_summary: str | None = None,
    ) -> AgentRun:
        """Create and persist an AgentRun marking the start of an agent execution.

        ``started_at`` is set explicitly client-side (rather than relying on the
        server_default) so ``duration_ms`` can be computed later without a refresh.
        The caller controls the transaction; this method only flushes.
        """
        run = AgentRun(
            test_failure_id=test_failure_id,
            agent_name=agent_name,
            status=status,
            input_summary=input_summary,
            started_at=datetime.now(UTC),
        )
        session.add(run)
        await session.flush()
        logger.debug(
            "agent_run.created",
            agent_run_id=str(run.id),
            test_failure_id=str(test_failure_id),
            agent_name=agent_name,
            status=status,
        )
        return run

    async def complete(
        self,
        session: AsyncSession,
        run_id: UUID,
        *,
        status: str,
        output_summary: str | None = None,
        tokens_used: int | None = None,
    ) -> AgentRun | None:
        """Mark an AgentRun as finished, recording its duration.

        Returns ``None`` if no AgentRun exists with the given id.
        The caller controls the transaction; this method only flushes.
        """
        stmt = select(AgentRun).where(AgentRun.id == run_id)
        result = await session.execute(stmt)
        run = result.scalar_one_or_none()
        if run is None:
            logger.debug("agent_run.complete_missing", agent_run_id=str(run_id))
            return None

        completed_at = datetime.now(UTC)
        run.completed_at = completed_at
        run.status = status
        if output_summary is not None:
            run.output_summary = output_summary
        if tokens_used is not None:
            run.tokens_used = tokens_used
        run.duration_ms = int((completed_at - run.started_at).total_seconds() * 1000)

        await session.flush()
        logger.debug(
            "agent_run.completed",
            agent_run_id=str(run.id),
            status=status,
            duration_ms=run.duration_ms,
        )
        return run
