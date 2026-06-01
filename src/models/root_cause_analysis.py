from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class RootCauseAnalysis(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "root_cause_analyses"

    test_failure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_failures.id", ondelete="CASCADE"),
        nullable=False,
    )
    pipeline_event_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_events.id", ondelete="SET NULL"),
        nullable=True,
    )
    root_cause_summary: Mapped[str] = mapped_column(Text, nullable=False)
    root_cause_category: Mapped[str] = mapped_column(String(50), nullable=False)
    likely_cause_files: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    investigation_steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    model_used: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index("ix_root_cause_analyses_test_failure_id", "test_failure_id"),
        Index("ix_root_cause_analyses_pipeline_event_id", "pipeline_event_id"),
        Index("ix_root_cause_analyses_root_cause_category", "root_cause_category"),
    )
