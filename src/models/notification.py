import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.test_failure import TestFailure


class Notification(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "notifications"

    test_failure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_failures.id", ondelete="CASCADE"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    recipient: Mapped[str | None] = mapped_column(String(500), nullable=True)
    message_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    # Relationships
    test_failure: Mapped["TestFailure"] = relationship(
        "TestFailure",
        back_populates="notifications",
        lazy="selectin",
    )
