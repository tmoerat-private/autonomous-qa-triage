from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories.release_score_repo import ReleaseScoreRepository
from src.models.release_score import ReleaseScore


async def get_release_score(
    db: AsyncSession,
    commit_sha: str,
    repository: str,
) -> ReleaseScore | None:
    return await ReleaseScoreRepository().get_by_commit(db, commit_sha, repository)


async def get_recent_scores(
    db: AsyncSession,
    repository: str,
    limit: int = 10,
) -> list[ReleaseScore]:
    return await ReleaseScoreRepository().get_recent(db, repository, limit=limit)
