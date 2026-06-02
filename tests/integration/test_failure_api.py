"""Integration tests for routes in failures.py NOT already covered by
tests/integration/test_failures_api.py.

Covered here:
  POST   /api/v1/failures/{id}/rerun
  GET    /api/v1/failures/{id}/suggestion
  PATCH  /api/v1/failures/{id}/suggestion
  GET    /api/v1/failures/{id}/root-cause
  POST   /api/v1/failures/{id}/screenshots
  GET    /api/v1/failures/{id}/screenshots
  GET    /api/v1/screenshots/{id}/file

All tests use a real PostgreSQL test database with transaction rollback per
test (conftest.py).  External CI API calls are intercepted with respx.
"""
from __future__ import annotations

import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from src.models.heal_suggestion import HealSuggestion
from src.models.root_cause_analysis import RootCauseAnalysis
from src.models.test_screenshot import TestScreenshot
from tests.factories import PipelineEventFactory, TestFailureFactory

# ---------------------------------------------------------------------------
# Helpers — seed objects into the shared db_session
# ---------------------------------------------------------------------------


async def _seed_event(db_session, **kwargs):
    event = PipelineEventFactory(**kwargs)
    db_session.add(event)
    await db_session.flush()
    return event


async def _seed_failure(db_session, pipeline_event=None, **kwargs):
    if pipeline_event is None:
        pipeline_event = await _seed_event(db_session)
    failure = TestFailureFactory(
        pipeline_event=pipeline_event,
        pipeline_event_id=pipeline_event.id,
        **kwargs,
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


async def _seed_suggestion(db_session, failure, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        suggestion="Replace assertEqual with assertAlmostEqual.",
        confidence=0.88,
        affected_file="tests/test_checkout.py",
        fix_snippet="self.assertAlmostEqual(result, expected, places=2)",
        accepted=None,
        model_used="claude-sonnet-4-20250514",
        tokens_used=512,
    )
    defaults.update(kwargs)
    suggestion = HealSuggestion(**defaults)
    db_session.add(suggestion)
    await db_session.flush()
    return suggestion


async def _seed_root_cause(db_session, failure, **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        pipeline_event_id=failure.pipeline_event_id,
        root_cause_summary="Null pointer dereference in checkout flow.",
        root_cause_category="product_bug",
        likely_cause_files=["src/checkout.py"],
        investigation_steps=["Check null guard at line 42."],
        model_used="claude-sonnet-4-20250514",
    )
    defaults.update(kwargs)
    analysis = RootCauseAnalysis(**defaults)
    db_session.add(analysis)
    await db_session.flush()
    return analysis


async def _seed_screenshot(db_session, failure, storage_path: str = "/tmp/fake.png", **kwargs):
    defaults = dict(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        original_filename="screenshot.png",
        content_type="image/png",
        storage_path=storage_path,
        file_size_bytes=1024,
    )
    defaults.update(kwargs)
    screenshot = TestScreenshot(**defaults)
    db_session.add(screenshot)
    await db_session.flush()
    return screenshot


# ===========================================================================
# POST /api/v1/failures/{id}/rerun — jenkins
# ===========================================================================


@pytest.mark.asyncio
async def test_rerun_jenkins_returns_200_and_job_id(client, db_session):
    """POST /rerun for a Jenkins failure calls JenkinsClient.trigger_rerun and returns 200."""
    event = await _seed_event(
        db_session,
        provider="jenkins",
        provider_build_id="101",
        pipeline_name="my-pipeline",
        repository="org/repo",
    )
    failure = await _seed_failure(db_session, pipeline_event=event)

    with patch(
        "src.integrations.jenkins.client.JenkinsClient"
    ) as MockJenkins:
        mock_instance = MockJenkins.return_value.__aenter__.return_value
        mock_instance.trigger_rerun = AsyncMock(
            return_value={"job_name": "my-pipeline", "build_number": 102}
        )

        response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["provider"] == "jenkins"
    assert body["failure_id"] == str(failure.id)


@pytest.mark.asyncio
async def test_rerun_github_actions_returns_200_and_run_id(client, db_session):
    """POST /rerun for a GitHub Actions failure calls GitHubActionsClient.trigger_rerun."""
    event = await _seed_event(
        db_session,
        provider="github_actions",
        provider_build_id="9999",
        repository="org/service",
    )
    failure = await _seed_failure(db_session, pipeline_event=event)

    with patch(
        "src.integrations.github_actions.client.GitHubActionsClient"
    ) as MockGHA:
        mock_instance = MockGHA.return_value.__aenter__.return_value
        mock_instance.trigger_rerun = AsyncMock(
            return_value={"run_id": 10000}
        )

        response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 200
    body = response.json()
    assert body["triggered"] is True
    assert body["provider"] == "github_actions"
    assert body["failure_id"] == str(failure.id)


@pytest.mark.asyncio
async def test_rerun_returns_404_for_unknown_failure(client, db_session):
    """POST /rerun returns 404 when the failure_id does not exist."""
    response = await client.post(f"/api/v1/failures/{uuid.uuid4()}/rerun")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


@pytest.mark.asyncio
async def test_rerun_returns_400_for_unsupported_provider(client, db_session):
    """POST /rerun returns 400 when the pipeline event provider is not supported."""
    event = await _seed_event(
        db_session,
        provider="circleci",
        provider_build_id="55",
        repository="org/repo",
    )
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 400
    assert "unsupported provider" in response.json()["detail"]


@pytest.mark.asyncio
async def test_rerun_returns_502_when_ci_api_fails(client, db_session):
    """POST /rerun returns 502 when the CI client raises an HTTP error."""
    import httpx

    event = await _seed_event(
        db_session,
        provider="jenkins",
        provider_build_id="77",
        pipeline_name="flaky-job",
        repository="org/repo",
    )
    failure = await _seed_failure(db_session, pipeline_event=event)

    with patch("src.integrations.jenkins.client.JenkinsClient") as MockJenkins:
        mock_instance = MockJenkins.return_value.__aenter__.return_value
        mock_instance.trigger_rerun = AsyncMock(
            side_effect=httpx.HTTPStatusError(
                "502",
                request=httpx.Request("POST", "http://jenkins.example.com"),
                response=httpx.Response(502),
            )
        )

        response = await client.post(f"/api/v1/failures/{failure.id}/rerun")

    assert response.status_code == 502
    assert "CI API unreachable" in response.json()["detail"]


# ===========================================================================
# GET /api/v1/failures/{id}/suggestion
# ===========================================================================


@pytest.mark.asyncio
async def test_get_suggestion_returns_200_with_fields(client, db_session):
    """GET /suggestion returns the most recent HealSuggestion for the failure."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_suggestion(db_session, failure, suggestion="Fix the assertion.")

    response = await client.get(f"/api/v1/failures/{failure.id}/suggestion")

    assert response.status_code == 200
    body = response.json()
    assert body["test_failure_id"] == str(failure.id)
    assert body["suggestion"] == "Fix the assertion."
    assert "confidence" in body
    assert "model_used" in body
    assert "accepted" in body


@pytest.mark.asyncio
async def test_get_suggestion_returns_404_when_no_suggestion_exists(client, db_session):
    """GET /suggestion returns 404 when no HealSuggestion row exists for the failure."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}/suggestion")

    assert response.status_code == 404
    assert "no suggestion found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_suggestion_returns_404_for_unknown_failure(client, db_session):
    """GET /suggestion returns 404 when the failure_id does not exist."""
    response = await client.get(f"/api/v1/failures/{uuid.uuid4()}/suggestion")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# PATCH /api/v1/failures/{id}/suggestion
# ===========================================================================


@pytest.mark.asyncio
async def test_patch_suggestion_accept_sets_accepted_true(client, db_session):
    """PATCH /suggestion with accepted=true persists accepted=True and returns 200."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_suggestion(db_session, failure)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": True},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["accepted"] is True
    assert body["test_failure_id"] == str(failure.id)


@pytest.mark.asyncio
async def test_patch_suggestion_reject_sets_accepted_false(client, db_session):
    """PATCH /suggestion with accepted=false persists accepted=False and returns 200."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_suggestion(db_session, failure)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": False},
    )

    assert response.status_code == 200
    assert response.json()["accepted"] is False


@pytest.mark.asyncio
async def test_patch_suggestion_returns_404_when_no_suggestion_exists(client, db_session):
    """PATCH /suggestion returns 404 when no HealSuggestion exists for the failure."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.patch(
        f"/api/v1/failures/{failure.id}/suggestion",
        json={"accepted": True},
    )

    assert response.status_code == 404
    assert "no suggestion found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_patch_suggestion_returns_404_for_unknown_failure(client, db_session):
    """PATCH /suggestion returns 404 when the failure_id does not exist."""
    response = await client.patch(
        f"/api/v1/failures/{uuid.uuid4()}/suggestion",
        json={"accepted": True},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# GET /api/v1/failures/{id}/root-cause
# ===========================================================================


@pytest.mark.asyncio
async def test_get_root_cause_returns_200_with_fields(client, db_session):
    """GET /root-cause returns the most recent RootCauseAnalysis for the failure."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_root_cause(
        db_session, failure, root_cause_summary="DB connection pool exhausted."
    )

    response = await client.get(f"/api/v1/failures/{failure.id}/root-cause")

    assert response.status_code == 200
    body = response.json()
    assert body["test_failure_id"] == str(failure.id)
    assert body["root_cause_summary"] == "DB connection pool exhausted."
    assert "root_cause_category" in body
    assert "likely_cause_files" in body
    assert "investigation_steps" in body


@pytest.mark.asyncio
async def test_get_root_cause_returns_404_when_no_analysis_exists(client, db_session):
    """GET /root-cause returns 404 when no RootCauseAnalysis row exists."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}/root-cause")

    assert response.status_code == 404
    assert "no root cause analysis found" in response.json()["detail"]


@pytest.mark.asyncio
async def test_get_root_cause_returns_404_for_unknown_failure(client, db_session):
    """GET /root-cause returns 404 when the failure_id does not exist."""
    response = await client.get(f"/api/v1/failures/{uuid.uuid4()}/root-cause")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# POST /api/v1/failures/{id}/screenshots
# ===========================================================================


@pytest.mark.asyncio
async def test_upload_screenshot_returns_201_with_metadata(client, db_session, tmp_path):
    """POST /screenshots with a valid PNG returns 201 and ScreenshotResponse."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8  # minimal PNG-like header

    with patch("src.services.screenshot_service.get_settings") as mock_settings:
        mock_settings.return_value.screenshot_storage_path = str(tmp_path)
        mock_settings.return_value.max_screenshot_size_bytes = 10_485_760

        response = await client.post(
            f"/api/v1/failures/{failure.id}/screenshots",
            files={"file": ("capture.png", io.BytesIO(png_bytes), "image/png")},
        )

    assert response.status_code == 201
    body = response.json()
    assert body["test_failure_id"] == str(failure.id)
    assert body["original_filename"] == "capture.png"
    assert body["content_type"] == "image/png"
    assert body["file_size_bytes"] == len(png_bytes)
    assert "id" in body
    assert "storage_path" in body


@pytest.mark.asyncio
async def test_upload_screenshot_returns_400_for_unsupported_content_type(client, db_session, tmp_path):
    """POST /screenshots with a non-image content type returns 400."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    with patch("src.services.screenshot_service.get_settings") as mock_settings:
        mock_settings.return_value.screenshot_storage_path = str(tmp_path)
        mock_settings.return_value.max_screenshot_size_bytes = 10_485_760

        response = await client.post(
            f"/api/v1/failures/{failure.id}/screenshots",
            files={"file": ("report.pdf", io.BytesIO(b"%PDF-1.4"), "application/pdf")},
        )

    assert response.status_code == 400
    assert "unsupported content type" in response.json()["detail"]


@pytest.mark.asyncio
async def test_upload_screenshot_returns_404_for_unknown_failure(client, db_session):
    """POST /screenshots returns 404 when the failure does not exist."""
    response = await client.post(
        f"/api/v1/failures/{uuid.uuid4()}/screenshots",
        files={"file": ("x.png", io.BytesIO(b"\x89PNG"), "image/png")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# GET /api/v1/failures/{id}/screenshots
# ===========================================================================


@pytest.mark.asyncio
async def test_list_screenshots_returns_all_for_failure(client, db_session, tmp_path):
    """GET /screenshots returns every screenshot associated with the failure."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    shot_a = await _seed_screenshot(
        db_session, failure, storage_path=str(tmp_path / "a.png"), original_filename="a.png"
    )
    shot_b = await _seed_screenshot(
        db_session, failure, storage_path=str(tmp_path / "b.png"), original_filename="b.png"
    )

    response = await client.get(f"/api/v1/failures/{failure.id}/screenshots")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    returned_ids = {item["id"] for item in body}
    assert str(shot_a.id) in returned_ids
    assert str(shot_b.id) in returned_ids


@pytest.mark.asyncio
async def test_list_screenshots_returns_empty_list_when_none_exist(client, db_session):
    """GET /screenshots returns [] when no screenshots have been uploaded."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}/screenshots")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_screenshots_returns_404_for_unknown_failure(client, db_session):
    """GET /screenshots returns 404 when the failure does not exist."""
    response = await client.get(f"/api/v1/failures/{uuid.uuid4()}/screenshots")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# GET /api/v1/screenshots/{id}/file  (screenshots_router)
# ===========================================================================


@pytest.mark.asyncio
async def test_get_screenshot_file_streams_image(client, db_session, tmp_path):
    """GET /screenshots/{id}/file returns the image bytes for a stored screenshot."""
    png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    dest = tmp_path / "test_shot.png"
    dest.write_bytes(png_bytes)

    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    shot = await _seed_screenshot(
        db_session,
        failure,
        storage_path=str(dest),
        original_filename="test_shot.png",
        content_type="image/png",
        file_size_bytes=len(png_bytes),
    )

    response = await client.get(f"/api/v1/screenshots/{shot.id}/file")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content == png_bytes


@pytest.mark.asyncio
async def test_get_screenshot_file_returns_404_for_unknown_screenshot(client, db_session):
    """GET /screenshots/{id}/file returns 404 when the screenshot row does not exist."""
    response = await client.get(f"/api/v1/screenshots/{uuid.uuid4()}/file")

    assert response.status_code == 404
    assert response.json()["detail"] == "screenshot not found"


@pytest.mark.asyncio
async def test_get_screenshot_file_returns_404_when_file_missing_on_disk(client, db_session, tmp_path):
    """GET /screenshots/{id}/file returns 404 when the DB row exists but file is gone."""
    event = await _seed_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    # Point to a path that does not actually exist on disk
    shot = await _seed_screenshot(
        db_session,
        failure,
        storage_path=str(tmp_path / "deleted_file.png"),
        original_filename="deleted_file.png",
    )

    response = await client.get(f"/api/v1/screenshots/{shot.id}/file")

    assert response.status_code == 404
    assert "screenshot file not found" in response.json()["detail"]
