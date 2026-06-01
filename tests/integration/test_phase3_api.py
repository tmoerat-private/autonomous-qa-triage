"""Integration tests for Phase 3 API endpoints: suggestion and rerun."""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.heal_suggestion import HealSuggestion
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(
    db_session: AsyncSession,
    provider: str = "jenkins",
    pipeline_name: str = "build-tests",
    provider_build_id: str = "42",
    repository: str = "org/repo",
) -> tuple[PipelineEvent, TestFailure]:
    """Insert a PipelineEvent + TestFailure and return both."""
    event = PipelineEvent(
        provider=provider,
        provider_build_id=provider_build_id,
        repository=repository,
        branch="main",
        commit_sha="abc123",
        pipeline_name=pipeline_name,
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_checkout_total",
        error_message="AssertionError: expected 99.99 but got 0.00",
        stack_trace="File tests/test_cart.py, line 12\nAssertionError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return event, failure


async def _add_suggestion(
    db_session: AsyncSession,
    failure: TestFailure,
    suggestion: str = "Fix the pool size",
    confidence: float = 0.85,
    affected_file: str | None = "src/db/session.py",
    fix_snippet: str | None = "pool_size=20",
    accepted: bool | None = None,
) -> HealSuggestion:
    """Seed a HealSuggestion record and return it."""
    record = HealSuggestion(
        test_failure_id=failure.id,
        suggestion=suggestion,
        confidence=confidence,
        affected_file=affected_file,
        fix_snippet=fix_snippet,
        accepted=accepted,
        model_used="claude-sonnet-4-20250514",
    )
    db_session.add(record)
    await db_session.flush()
    return record


def _make_jenkins_client_mock(
    job_name: str = "build-tests",
    build_number: int = 42,
) -> MagicMock:
    """Return a mock JenkinsClient usable as an async context manager."""
    mock_client = MagicMock()
    mock_client.trigger_rerun = AsyncMock(
        return_value={"triggered": True, "job_name": job_name, "build_number": build_number}
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_client)


# ---------------------------------------------------------------------------
# Suggestion GET tests
# ---------------------------------------------------------------------------


async def test_get_suggestion_not_found(client: AsyncClient, db_session: AsyncSession):
    """GET /failures/{id}/suggestion returns 404 when failure has no suggestion."""
    _, failure = await _make_failure(db_session)

    response = await client.get(f"/api/v1/failures/{failure.id}/suggestion")

    assert response.status_code == 404


async def test_get_suggestion_returns_latest(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /failures/{id}/suggestion returns 200 with suggestion fields when record exists."""
    _, failure = await _make_failure(db_session)
    await _add_suggestion(
        db_session,
        failure,
        suggestion="Fix the pool size",
        confidence=0.85,
        affected_file="src/db/session.py",
        fix_snippet="pool_size=20",
    )

    response = await client.get(f"/api/v1/failures/{failure.id}/suggestion")

    assert response.status_code == 200
    body = response.json()
    assert body["suggestion"] == "Fix the pool size"
    assert body["confidence"] == 0.85
    assert body["affected_file"] == "src/db/session.py"
    assert body["fix_snippet"] == "pool_size=20"
    assert body["accepted"] is None


# ---------------------------------------------------------------------------
# Suggestion PATCH tests
# ---------------------------------------------------------------------------


async def test_patch_suggestion_accept(
    client: AsyncClient, db_session: AsyncSession
):
    """PATCH /failures/{id}/suggestion with accepted=true returns updated record."""
    _, failure = await _make_failure(db_session)
    await _add_suggestion(db_session, failure)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True


async def test_patch_suggestion_reject(
    client: AsyncClient, db_session: AsyncSession
):
    """PATCH /failures/{id}/suggestion with accepted=false returns updated record."""
    _, failure = await _make_failure(db_session)
    await _add_suggestion(db_session, failure)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is False


async def test_patch_suggestion_no_suggestion(
    client: AsyncClient, db_session: AsyncSession
):
    """PATCH /failures/{id}/suggestion returns 404 when failure has no suggestion."""
    _, failure = await _make_failure(db_session)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": True},
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Manual rerun tests
# ---------------------------------------------------------------------------


async def test_manual_rerun_unknown_failure(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /failures/{random_uuid}/rerun returns 404 for unknown failure."""
    random_id = uuid.uuid4()

    response = await client.post(f"/api/v1/failures/{random_id}/rerun")

    assert response.status_code == 404


async def test_manual_rerun_success_jenkins(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /failures/{id}/rerun triggers CI and returns triggered=True for jenkins."""
    _, failure = await _make_failure(
        db_session,
        provider="jenkins",
        pipeline_name="build-tests",
        provider_build_id="42",
    )
    mock_jenkins_cls = _make_jenkins_client_mock(job_name="build-tests", build_number=42)

    with patch("src.integrations.jenkins.client.JenkinsClient", mock_jenkins_cls):
        response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["provider"] == "jenkins"


async def test_manual_rerun_ci_unreachable(
    client: AsyncClient, db_session: AsyncSession
):
    """POST /failures/{id}/rerun returns 502 when CI client raises an exception."""
    _, failure = await _make_failure(
        db_session,
        provider="jenkins",
        pipeline_name="build-tests",
        provider_build_id="42",
    )

    mock_client = MagicMock()
    mock_client.trigger_rerun = AsyncMock(side_effect=Exception("timeout"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_jenkins_cls = MagicMock(return_value=mock_client)

    with patch("src.integrations.jenkins.client.JenkinsClient", mock_jenkins_cls):
        response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 502
