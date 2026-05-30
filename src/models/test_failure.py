import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.agent_run import AgentRun
    from src.models.failure_classification import FailureClassification
    from src.models.notification import Notification
    from src.models.pipeline_event import PipelineEvent
    from src.models.triage_ticket import TriageTicket


class TestFailure(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "test_failures"

    pipeline_event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("pipeline_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    test_name: Mapped[str] = mapped_column(String(1000), nullable=False)
    test_suite: Mapped[str | None] = mapped_column(String(500), nullable=True)
    test_file: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stack_trace: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="new")

    # Relationships
    pipeline_event: Mapped["PipelineEvent"] = relationship(
        "PipelineEvent",
        back_populates="test_failures",
        lazy="selectin",
    )
    classification: Mapped["FailureClassification | None"] = relationship(
        "FailureClassification",
        back_populates="test_failure",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    triage_ticket: Mapped["TriageTicket | None"] = relationship(
        "TriageTicket",
        back_populates="test_failure",
        uselist=False,
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    agent_runs: Mapped[list["AgentRun"]] = relationship(
        "AgentRun",
        back_populates="test_failure",
        lazy="selectin",
        cascade="all, delete-orphan",
    )
    notifications: Mapped[list["Notification"]] = relationship(
        "Notification",
        back_populates="test_failure",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_test_failures_pipeline_event_id", "pipeline_event_id"),
        Index("ix_test_failures_status", "status"),
    )
