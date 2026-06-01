from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.rerun_request import RerunRequest

logger = structlog.get_logger(__name__)


class RerunRepository:
    """Data access layer for RerunRequest records."""

    async def create(
        self, session: AsyncSession, **kwargs
    ) -> RerunRequest:
        """Create and persist a RerunRequest.

        The caller controls the transaction; this method only flushes.
        """
        rerun = RerunRequest(**kwargs)
        session.add(rerun)
        await session.flush()
        logger.info(
            "rerun_request.created",
            rerun_id=str(rerun.id),
            test_failure_id=str(rerun.test_failure_id),
            provider=rerun.provider,
            status=rerun.status,
        )
        return rerun

    async def get_by_failure_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> list[RerunRequest]:
        """Return all RerunRequests for a test failure ordered by triggered_at DESC."""
        stmt = (
            select(RerunRequest)
            .where(RerunRequest.test_failure_id == failure_id)
            .order_by(RerunRequest.triggered_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self, session: AsyncSession, id: UUID, status: str
    ) -> RerunRequest | None:
        """Set the status field on a RerunRequest.

        Returns None if the record does not exist.
        The caller controls the transaction; this method only flushes.
        """
        stmt = select(RerunRequest).where(RerunRequest.id == id)
        result = await session.execute(stmt)
        rerun = result.scalar_one_or_none()
        if rerun is None:
            return None
        rerun.status = status
        await session.flush()
        logger.info(
            "rerun_request.status_updated",
            rerun_id=str(id),
            status=status,
        )
        return rerun
