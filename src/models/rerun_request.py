import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.test_failure import TestFailure


class RerunRequest(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "rerun_requests"

    test_failure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_failures.id", ondelete="CASCADE"),
        nullable=False,
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    triggered_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    trigger_reason: Mapped[str] = mapped_column(
        String(50), nullable=False, default="flaky_detected"
    )
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default="triggered"
    )
    triggered_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    test_failure: Mapped["TestFailure"] = relationship(
        "TestFailure",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_rerun_requests_test_failure_id", "test_failure_id"),
    )
