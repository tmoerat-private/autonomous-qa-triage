import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from src.models.test_failure import TestFailure


class HealSuggestion(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "heal_suggestions"

    test_failure_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("test_failures.id", ondelete="CASCADE"),
        nullable=False,
    )
    suggestion: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    affected_file: Mapped[str | None] = mapped_column(String(500), nullable=True)
    fix_snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    accepted: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_used: Mapped[str] = mapped_column(String(100), nullable=False)
    tokens_used: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Relationships
    test_failure: Mapped["TestFailure"] = relationship(
        "TestFailure",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_heal_suggestions_test_failure_id", "test_failure_id"),
    )
