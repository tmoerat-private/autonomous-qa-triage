from datetime import datetime

from sqlalchemy import TIMESTAMP, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class ErrorSignature(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "error_signatures"

    signature_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    normalized_error: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    occurrence_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    embedding_id: Mapped[str | None] = mapped_column(String(255), nullable=True)

    __table_args__ = (
        UniqueConstraint("signature_hash", name="uq_error_signatures_signature_hash"),
    )
