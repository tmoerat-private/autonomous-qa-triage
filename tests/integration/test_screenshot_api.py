"""Integration tests for screenshot upload, list, and file-serve endpoints."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
from src.models.test_screenshot import TestScreenshot

# Minimal 1x1 transparent PNG (67 bytes -- well-formed, broadly accepted by httpx)
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01"
    b"\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure and return the failure."""
    event = PipelineEvent(
        provider="jenkins",
        provider_build_id="1",
        repository="org/r",
        branch="main",
        commit_sha="abc",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_login",
        error_message="err",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _mock_settings(tmp_path, max_bytes: int = 10_485_760) -> MagicMock:
    """Return a MagicMock settings object with a writable storage path."""
    settings = MagicMock()
    settings.screenshot_storage_path = str(tmp_path)
    settings.max_screenshot_size_bytes = max_bytes
    return settings


# ---------------------------------------------------------------------------
# Upload tests
# ---------------------------------------------------------------------------


async def test_upload_screenshot_png(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """POST /{id}/screenshots with a valid PNG returns 201 with screenshot metadata."""
    failure = await _make_failure(db_session)

    with patch(
        "src.services.screenshot_service.get_settings",
        return_value=_mock_settings(tmp_path),
    ):
        response = await client.post(
            f"/api/v1/failures/{failure.id}/screenshots",
            files={"file": ("test.png", _PNG_BYTES, "image/png")},
        )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["test_failure_id"] == str(failure.id)
    assert body["original_filename"] == "test.png"
    assert body["content_type"] == "image/png"


async def test_upload_screenshot_invalid_type(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """POST with an unsupported MIME type returns 400."""
    failure = await _make_failure(db_session)

    with patch(
        "src.services.screenshot_service.get_settings",
        return_value=_mock_settings(tmp_path),
    ):
        response = await client.post(
            f"/api/v1/failures/{failure.id}/screenshots",
            files={"file": ("doc.pdf", b"%PDF-1.4", "application/pdf")},
        )

    assert response.status_code == 400
    assert "unsupported content type" in response.json()["detail"]


async def test_upload_screenshot_too_large(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """POST with data exceeding max_screenshot_size_bytes returns 400."""
    failure = await _make_failure(db_session)

    # Set a 10-byte limit then send 20 bytes
    with patch(
        "src.services.screenshot_service.get_settings",
        return_value=_mock_settings(tmp_path, max_bytes=10),
    ):
        response = await client.post(
            f"/api/v1/failures/{failure.id}/screenshots",
            files={"file": ("big.png", b"\x89PNG" + b"\x00" * 16, "image/png")},
        )

    assert response.status_code == 400
    assert "too large" in response.json()["detail"]


async def test_upload_screenshot_failure_not_found(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """POST with an unknown failure UUID returns 404."""
    unknown_id = uuid.uuid4()

    with patch(
        "src.services.screenshot_service.get_settings",
        return_value=_mock_settings(tmp_path),
    ):
        response = await client.post(
            f"/api/v1/failures/{unknown_id}/screenshots",
            files={"file": ("test.png", _PNG_BYTES, "image/png")},
        )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


async def test_list_screenshots_empty(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /{id}/screenshots with no uploads returns 200 with an empty list."""
    failure = await _make_failure(db_session)

    response = await client.get(f"/api/v1/failures/{failure.id}/screenshots")

    assert response.status_code == 200
    assert response.json() == []


async def test_list_screenshots_with_data(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """GET /{id}/screenshots returns a list containing the seeded screenshot."""
    failure = await _make_failure(db_session)

    # Seed a screenshot record directly (no disk write needed for the list endpoint)
    img_path = tmp_path / "seeded.png"
    img_path.write_bytes(_PNG_BYTES)

    screenshot = TestScreenshot(
        test_failure_id=failure.id,
        original_filename="seeded.png",
        content_type="image/png",
        storage_path=str(img_path),
        file_size_bytes=len(_PNG_BYTES),
    )
    db_session.add(screenshot)
    await db_session.flush()

    response = await client.get(f"/api/v1/failures/{failure.id}/screenshots")

    assert response.status_code == 200
    items = response.json()
    assert len(items) == 1
    assert items[0]["original_filename"] == "seeded.png"
    assert items[0]["content_type"] == "image/png"


# ---------------------------------------------------------------------------
# File-serve tests
# ---------------------------------------------------------------------------


async def test_get_screenshot_file(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """GET /screenshots/{id}/file returns 200 with the exact file bytes."""
    failure = await _make_failure(db_session)

    img_path = tmp_path / "shot.png"
    img_path.write_bytes(b"PNG_DATA")

    screenshot = TestScreenshot(
        test_failure_id=failure.id,
        original_filename="shot.png",
        content_type="image/png",
        storage_path=str(img_path),
        file_size_bytes=8,
    )
    db_session.add(screenshot)
    await db_session.flush()

    response = await client.get(f"/api/v1/screenshots/{screenshot.id}/file")

    assert response.status_code == 200
    assert response.content == b"PNG_DATA"


async def test_get_screenshot_file_not_found(
    client: AsyncClient, db_session: AsyncSession
):
    """GET /screenshots/{id}/file with an unknown UUID returns 404."""
    unknown_id = uuid.uuid4()

    response = await client.get(f"/api/v1/screenshots/{unknown_id}/file")

    assert response.status_code == 404


async def test_get_screenshot_file_missing_on_disk(
    client: AsyncClient, db_session: AsyncSession, tmp_path
):
    """GET /screenshots/{id}/file returns 404 when the DB record exists but the file is gone."""
    failure = await _make_failure(db_session)

    screenshot = TestScreenshot(
        test_failure_id=failure.id,
        original_filename="gone.png",
        content_type="image/png",
        storage_path=str(tmp_path / "does_not_exist.png"),
        file_size_bytes=0,
    )
    db_session.add(screenshot)
    await db_session.flush()

    response = await client.get(f"/api/v1/screenshots/{screenshot.id}/file")

    assert response.status_code == 404
