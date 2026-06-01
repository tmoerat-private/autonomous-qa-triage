from __future__ import annotations

from datetime import datetime

from sqlalchemy import TIMESTAMP, Float, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.models.base import Base, TimestampMixin, UUIDMixin


class ReleaseScore(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "release_scores"

    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    repository: Mapped[str] = mapped_column(String(500), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    total_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    product_bug_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flaky_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    env_issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    infra_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "commit_sha", "repository", name="uq_release_scores_commit_repo"
        ),
        Index("ix_release_scores_commit_sha", "commit_sha"),
        Index("ix_release_scores_repository", "repository"),
        Index("ix_release_scores_scored_at", "scored_at"),
    )
