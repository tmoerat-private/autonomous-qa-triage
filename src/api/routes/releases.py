from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import DbSession
from src.schemas.agent_schemas import ReleaseScoreResponse
from src.services import release_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/releases", tags=["releases"])


@router.get("/recent", response_model=list[ReleaseScoreResponse])
async def get_recent_scores(
    db: DbSession,
    repository: str = Query(...),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[ReleaseScoreResponse]:
    """Return recent release scores for a repository, newest first.

    Requires the `repository` query parameter.
    Returns an empty list if no scores exist.
    """
    scores = await release_service.get_recent_scores(db, repository, limit=limit)
    logger.info("releases.recent.retrieved", repository=repository, count=len(scores))
    return [ReleaseScoreResponse.model_validate(s) for s in scores]


@router.get("/{commit_sha}/score", response_model=ReleaseScoreResponse)
async def get_release_score(
    commit_sha: str,
    db: DbSession,
    repository: str = Query(...),
) -> ReleaseScoreResponse:
    """Return the latest release risk score for a specific commit SHA.

    Requires the `repository` query parameter (e.g. `?repository=org/api-service`).
    Returns 404 if the commit has not been scored yet.
    """
    score = await release_service.get_release_score(db, commit_sha, repository)
    if score is None:
        raise HTTPException(status_code=404, detail="no score found for this commit")
    logger.info("releases.score.retrieved", commit_sha=commit_sha[:8], repository=repository)
    return ReleaseScoreResponse.model_validate(score)
