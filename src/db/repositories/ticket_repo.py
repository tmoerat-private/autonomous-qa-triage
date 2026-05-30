from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.triage_ticket import TriageTicket

logger = structlog.get_logger(__name__)


class TicketRepository:
    """Data access layer for TriageTicket records."""

    async def create(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
        provider: str,
        external_ticket_id: str,
        external_url: str,
        title: str,
        description: str | None = None,
        priority: str | None = None,
    ) -> TriageTicket:
        """Create and persist a TriageTicket linked to a TestFailure.

        The caller controls the transaction; this method only flushes.
        """
        ticket = TriageTicket(
            test_failure_id=test_failure_id,
            provider=provider,
            external_ticket_id=external_ticket_id,
            external_url=external_url,
            title=title,
            description=description,
            priority=priority,
        )
        session.add(ticket)
        await session.flush()
        logger.info(
            "triage_ticket.created",
            ticket_id=str(ticket.id),
            test_failure_id=str(test_failure_id),
            provider=provider,
            external_ticket_id=external_ticket_id,
        )
        return ticket

    async def get_by_failure_id(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
    ) -> TriageTicket | None:
        """Return the TriageTicket for a TestFailure, or None if not yet created."""
        stmt = select(TriageTicket).where(
            TriageTicket.test_failure_id == test_failure_id
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
