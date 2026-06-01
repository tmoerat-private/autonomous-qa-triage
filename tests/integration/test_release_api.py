"""Integration tests for GET /api/v1/releases/recent and GET /api/v1/releases/{commit_sha}/score.

All tests run against a real PostgreSQL test database with transaction rollback
per test (see conftest.py).  The `client` fixture wires the test db_session
into the FastAPI dependency so no separate mock is needed for DB calls.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.release_score import ReleaseScore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_score(
    db_session: AsyncSession,
    commit_sha: str = "abc123",
    repository: str = "org/repo",
    score: float = 45.0,
    risk_level: str = "medium",
    scored_at: datetime | None = None,
) -> ReleaseScore:
    """Insert a ReleaseScore record into the test DB and return it."""
    if scored_at is None:
        scored_at = datetime.now(UTC)

    rec = ReleaseScore(
        commit_sha=commit_sha,
        repository=repository,
        score=score,
        risk_level=risk_level,
        risk_summary="Test summary.",
        total_failures=3,
        product_bug_count=2,
        flaky_count=1,
        env_issue_count=0,
        infra_count=0,
        duplicate_count=0,
        avg_confidence=0.85,
        scored_at=scored_at,
    )
    db_session.add(rec)
    await db_session.flush()
    return rec


# ---------------------------------------------------------------------------
# GET /api/v1/releases/{commit_sha}/score
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_release_score_found(client, db_session):
    """GET /{commit_sha}/score returns 200 with the correct score fields."""
    await _seed_score(db_session, commit_sha="abc123", repository="org/repo", score=45.0, risk_level="medium")

    response = await client.get("/api/v1/releases/abc123/score?repository=org/repo")

    assert response.status_code == 200
    body = response.json()
    assert body["commit_sha"] == "abc123"
    assert body["risk_level"] == "medium"
    assert body["score"] == pytest.approx(45.0)


@pytest.mark.asyncio
async def test_get_release_score_not_found(client, db_session):
    """GET /{commit_sha}/score returns 404 when no score exists for that commit."""
    response = await client.get("/api/v1/releases/unknown-sha/score?repository=org/repo")

    assert response.status_code == 404
    assert "no score found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_release_score_missing_repository_param(client, db_session):
    """GET /{commit_sha}/score without ?repository= returns 422 (required query param)."""
    response = await client.get("/api/v1/releases/abc123/score")

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/releases/recent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_recent_scores_empty(client, db_session):
    """GET /recent returns an empty list when no scores exist for the repository."""
    response = await client.get("/api/v1/releases/recent?repository=org/repo")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_get_recent_scores_with_data(client, db_session):
    """GET /recent returns 3 records ordered newest first."""
    now = datetime.now(UTC)
    for i in range(3):
        await _seed_score(
            db_session,
            commit_sha=f"sha-{i}",
            repository="org/repo",
            scored_at=now - timedelta(hours=i),
        )

    response = await client.get("/api/v1/releases/recent?repository=org/repo&limit=10")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 3

    # Verify descending order: each record's scored_at must be >= the next one
    for idx in range(len(body) - 1):
        ts_current = datetime.fromisoformat(body[idx]["scored_at"])
        ts_next = datetime.fromisoformat(body[idx + 1]["scored_at"])
        assert ts_current >= ts_next, (
            f"Expected descending order but [{idx}]={ts_current} < [{idx + 1}]={ts_next}"
        )


@pytest.mark.asyncio
async def test_get_recent_scores_limit(client, db_session):
    """GET /recent?limit=3 returns exactly 3 records even when 5 exist."""
    now = datetime.now(UTC)
    for i in range(5):
        await _seed_score(
            db_session,
            commit_sha=f"sha-limit-{i}",
            repository="org/repo",
            scored_at=now - timedelta(hours=i),
        )

    response = await client.get("/api/v1/releases/recent?repository=org/repo&limit=3")

    assert response.status_code == 200
    assert len(response.json()) == 3


@pytest.mark.asyncio
async def test_get_recent_scores_missing_repository_param(client, db_session):
    """GET /recent without ?repository= returns 422 (required query param)."""
    response = await client.get("/api/v1/releases/recent")

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_recent_scores_filters_by_repository(client, db_session):
    """GET /recent only returns scores matching the requested repository."""
    now = datetime.now(UTC)
    await _seed_score(db_session, commit_sha="sha-a", repository="org/service-a", scored_at=now)
    await _seed_score(db_session, commit_sha="sha-b", repository="org/service-b", scored_at=now)

    response = await client.get("/api/v1/releases/recent?repository=org/service-a")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["commit_sha"] == "sha-a"
    assert body[0]["repository"] == "org/service-a"


@pytest.mark.asyncio
async def test_get_release_score_response_has_required_fields(client, db_session):
    """The score response includes all ReleaseScoreResponse schema fields."""
    await _seed_score(db_session, commit_sha="full-sha", repository="org/repo")

    response = await client.get("/api/v1/releases/full-sha/score?repository=org/repo")

    assert response.status_code == 200
    body = response.json()
    required_fields = {
        "id",
        "commit_sha",
        "repository",
        "score",
        "risk_level",
        "risk_summary",
        "total_failures",
        "product_bug_count",
        "flaky_count",
        "env_issue_count",
        "infra_count",
        "duplicate_count",
        "avg_confidence",
        "scored_at",
    }
    missing = required_fields - set(body.keys())
    assert not missing, f"Response body is missing fields: {missing}"
