from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.root_cause_analysis import RootCauseAnalysis

logger = structlog.get_logger(__name__)


class RootCauseRepository:
    """Data access layer for RootCauseAnalysis records."""

    async def get_by_id(
        self, session: AsyncSession, id: UUID
    ) -> RootCauseAnalysis | None:
        """Return a RootCauseAnalysis by primary key, or None if not found."""
        stmt = select(RootCauseAnalysis).where(RootCauseAnalysis.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_failure_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> list[RootCauseAnalysis]:
        """Return all analyses for a test failure ordered by created_at DESC."""
        stmt = (
            select(RootCauseAnalysis)
            .where(RootCauseAnalysis.test_failure_id == failure_id)
            .order_by(RootCauseAnalysis.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_by_failure_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> RootCauseAnalysis | None:
        """Return the most recent analysis for a test failure, or None."""
        stmt = (
            select(RootCauseAnalysis)
            .where(RootCauseAnalysis.test_failure_id == failure_id)
            .order_by(RootCauseAnalysis.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def create(
        self, session: AsyncSession, **kwargs
    ) -> RootCauseAnalysis:
        """Create and persist a RootCauseAnalysis.

        The caller controls the transaction; this method only flushes.
        """
        analysis = RootCauseAnalysis(**kwargs)
        session.add(analysis)
        await session.flush()
        logger.info(
            "root_cause_analysis.created",
            analysis_id=str(analysis.id),
            test_failure_id=str(analysis.test_failure_id),
        )
        return analysis
