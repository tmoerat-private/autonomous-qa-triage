from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.release_score import ReleaseScore

logger = structlog.get_logger(__name__)


class ReleaseScoreRepository:
    """Data access layer for ReleaseScore records."""

    async def upsert(
        self,
        session: AsyncSession,
        commit_sha: str,
        repository: str,
        score: float,
        risk_level: str,
        risk_summary: str | None,
        total_failures: int,
        product_bug_count: int,
        flaky_count: int,
        env_issue_count: int,
        infra_count: int,
        duplicate_count: int,
        avg_confidence: float | None,
    ) -> ReleaseScore:
        """Insert or update a ReleaseScore for the given commit + repository pair.

        Uses PostgreSQL INSERT ... ON CONFLICT DO UPDATE so the record is
        created on first call and refreshed on subsequent calls without a
        separate SELECT.  The caller controls the transaction; this method
        only flushes.
        """
        now = datetime.now(UTC)
        stmt = (
            pg_insert(ReleaseScore)
            .values(
                commit_sha=commit_sha,
                repository=repository,
                score=score,
                risk_level=risk_level,
                risk_summary=risk_summary,
                total_failures=total_failures,
                product_bug_count=product_bug_count,
                flaky_count=flaky_count,
                env_issue_count=env_issue_count,
                infra_count=infra_count,
                duplicate_count=duplicate_count,
                avg_confidence=avg_confidence,
                scored_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_release_scores_commit_repo",
                set_={
                    "score": score,
                    "risk_level": risk_level,
                    "risk_summary": risk_summary,
                    "total_failures": total_failures,
                    "product_bug_count": product_bug_count,
                    "flaky_count": flaky_count,
                    "env_issue_count": env_issue_count,
                    "infra_count": infra_count,
                    "duplicate_count": duplicate_count,
                    "avg_confidence": avg_confidence,
                    "scored_at": now,
                },
            )
            .returning(ReleaseScore)
        )
        result = await session.execute(stmt)
        row = result.scalar_one()
        await session.flush()
        logger.info(
            "release_score.upserted",
            commit_sha=commit_sha,
            repository=repository,
            score=score,
            risk_level=risk_level,
        )
        return row

    async def get_by_commit(
        self,
        session: AsyncSession,
        commit_sha: str,
        repository: str,
    ) -> ReleaseScore | None:
        """Return the ReleaseScore for a specific commit + repository, or None."""
        stmt = select(ReleaseScore).where(
            ReleaseScore.commit_sha == commit_sha,
            ReleaseScore.repository == repository,
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_recent(
        self,
        session: AsyncSession,
        repository: str,
        limit: int = 10,
    ) -> list[ReleaseScore]:
        """Return the most recently scored releases for a repository."""
        stmt = (
            select(ReleaseScore)
            .where(ReleaseScore.repository == repository)
            .order_by(ReleaseScore.scored_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
