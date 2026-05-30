from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_event import PipelineEvent

logger = structlog.get_logger(__name__)


class PipelineEventRepository:
    """Data access layer for PipelineEvent records."""

    async def create(
        self,
        session: AsyncSession,
        provider: str,
        provider_build_id: str,
        repository: str | None,
        branch: str | None,
        commit_sha: str | None,
        pipeline_name: str | None,
        status: str,
        raw_payload: dict,
    ) -> PipelineEvent:
        """Create and persist a new PipelineEvent.

        The caller controls the transaction; this method only flushes.
        """
        event = PipelineEvent(
            provider=provider,
            provider_build_id=provider_build_id,
            repository=repository,
            branch=branch,
            commit_sha=commit_sha,
            pipeline_name=pipeline_name,
            status=status,
            raw_payload=raw_payload,
        )
        session.add(event)
        await session.flush()
        logger.info(
            "pipeline_event.created",
            event_id=str(event.id),
            provider=provider,
            provider_build_id=provider_build_id,
            status=status,
        )
        return event

    async def get_by_id(
        self, session: AsyncSession, event_id: UUID
    ) -> PipelineEvent | None:
        """Return a PipelineEvent by primary key, or None if not found."""
        stmt = select(PipelineEvent).where(PipelineEvent.id == event_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_provider_build(
        self, session: AsyncSession, provider: str, provider_build_id: str
    ) -> PipelineEvent | None:
        """Look up by (provider, provider_build_id) to detect duplicate webhooks."""
        stmt = select(PipelineEvent).where(
            PipelineEvent.provider == provider,
            PipelineEvent.provider_build_id == provider_build_id,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_status(
        self, session: AsyncSession, event_id: UUID, status: str
    ) -> PipelineEvent:
        """Update the status of a PipelineEvent in place.

        Raises ValueError if the event does not exist.
        """
        event = await self.get_by_id(session, event_id)
        if event is None:
            raise ValueError(f"PipelineEvent not found: {event_id}")
        event.status = status
        await session.flush()
        logger.info(
            "pipeline_event.status_updated",
            event_id=str(event_id),
            status=status,
        )
        return event

    async def list_recent(
        self, session: AsyncSession, limit: int = 50
    ) -> list[PipelineEvent]:
        """Return the most recent PipelineEvents ordered by received_at descending."""
        stmt = (
            select(PipelineEvent)
            .order_by(PipelineEvent.received_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
