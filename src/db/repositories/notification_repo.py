from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.notification import Notification

logger = structlog.get_logger(__name__)


class NotificationRepository:
    """Data access layer for Notification records."""

    async def create(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
        channel: str,
        recipient: str | None = None,
        message_type: str | None = None,
        external_message_id: str | None = None,
        sent_at: datetime | None = None,
    ) -> Notification:
        """Create and persist a single Notification record.

        The caller controls the transaction; this method only flushes.

        Args:
            session: Active async SQLAlchemy session.
            test_failure_id: FK to the associated TestFailure.
            channel: Notification channel, e.g. ``"slack"``.
            recipient: Channel ID, email address, or other destination string.
            message_type: Logical message type, e.g. ``"triage_result"``.
            external_message_id: Provider-assigned message ID (Slack ``ts``, etc.).
            sent_at: Timestamp when the notification was delivered, or ``None``.

        Returns:
            The newly created and flushed ``Notification`` instance.
        """
        notification = Notification(
            test_failure_id=test_failure_id,
            channel=channel,
            recipient=recipient,
            message_type=message_type,
            external_message_id=external_message_id,
            sent_at=sent_at,
        )
        session.add(notification)
        await session.flush()
        logger.info(
            "notification.created",
            notification_id=str(notification.id),
            test_failure_id=str(test_failure_id),
        )
        return notification

    async def get_by_failure_id(
        self,
        session: AsyncSession,
        test_failure_id: UUID,
    ) -> list[Notification]:
        """Return all Notifications associated with a given TestFailure.

        Args:
            session: Active async SQLAlchemy session.
            test_failure_id: FK to filter by.

        Returns:
            List of ``Notification`` instances (may be empty).
        """
        stmt = select(Notification).where(
            Notification.test_failure_id == test_failure_id
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
