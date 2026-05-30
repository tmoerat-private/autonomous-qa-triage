from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import TIMESTAMP, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.test_failure import TestFailure


class PipelineEvent(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "pipeline_events"

    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    provider_build_id: Mapped[str] = mapped_column(String(255), nullable=False)
    repository: Mapped[str | None] = mapped_column(String(500), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pipeline_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    received_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    test_failures: Mapped[list["TestFailure"]] = relationship(
        "TestFailure",
        back_populates="pipeline_event",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_pipeline_events_provider_build", "provider", "provider_build_id"),
        Index("ix_pipeline_events_received_at", "received_at"),
    )
