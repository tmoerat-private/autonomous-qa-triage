from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.heal_suggestion import HealSuggestion

logger = structlog.get_logger(__name__)


class HealSuggestionRepository:
    """Data access layer for HealSuggestion records."""

    async def get_by_id(
        self, session: AsyncSession, id: UUID
    ) -> HealSuggestion | None:
        """Return a HealSuggestion by primary key, or None if not found."""
        stmt = select(HealSuggestion).where(HealSuggestion.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_failure_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> list[HealSuggestion]:
        """Return all HealSuggestions for a test failure ordered by created_at DESC."""
        stmt = (
            select(HealSuggestion)
            .where(HealSuggestion.test_failure_id == failure_id)
            .order_by(HealSuggestion.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def create(
        self, session: AsyncSession, **kwargs
    ) -> HealSuggestion:
        """Create and persist a HealSuggestion.

        The caller controls the transaction; this method only flushes.
        """
        suggestion = HealSuggestion(**kwargs)
        session.add(suggestion)
        await session.flush()
        logger.info(
            "heal_suggestion.created",
            suggestion_id=str(suggestion.id),
            test_failure_id=str(suggestion.test_failure_id),
            confidence=suggestion.confidence,
        )
        return suggestion

    async def update_acceptance(
        self, session: AsyncSession, id: UUID, accepted: bool
    ) -> HealSuggestion | None:
        """Set the accepted field on a HealSuggestion.

        Returns None if the record does not exist.
        The caller controls the transaction; this method only flushes.
        """
        suggestion = await self.get_by_id(session, id)
        if suggestion is None:
            return None
        suggestion.accepted = accepted
        await session.flush()
        logger.info(
            "heal_suggestion.acceptance_updated",
            suggestion_id=str(id),
            accepted=accepted,
        )
        return suggestion
