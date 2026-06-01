from __future__ import annotations

import uuid
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.db.repositories.screenshot_repo import ScreenshotRepository
from src.models.test_screenshot import TestScreenshot

logger = structlog.get_logger(__name__)

ALLOWED_CONTENT_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
EXTENSION_MAP = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
}


async def save_screenshot(
    db: AsyncSession,
    failure_id: uuid.UUID,
    filename: str,
    content_type: str,
    data: bytes,
) -> TestScreenshot:
    """Validate, persist to disk, and save a TestScreenshot record.

    Args:
        db: Async SQLAlchemy session (caller controls transaction).
        failure_id: UUID of the TestFailure this screenshot belongs to.
        filename: The original filename submitted by the client.
        content_type: MIME type of the upload (must be an image type).
        data: Raw image bytes.

    Returns:
        The created TestScreenshot ORM record (not yet committed).

    Raises:
        ValueError: If content_type is not allowed or file exceeds max size.
    """
    settings = get_settings()

    if content_type not in ALLOWED_CONTENT_TYPES:
        raise ValueError(
            f"unsupported content type: {content_type}. "
            f"Allowed: {sorted(ALLOWED_CONTENT_TYPES)}"
        )

    if len(data) > settings.max_screenshot_size_bytes:
        raise ValueError(
            f"file too large: {len(data)} bytes exceeds limit of "
            f"{settings.max_screenshot_size_bytes}"
        )

    ext = EXTENSION_MAP[content_type]
    generated_name = f"{uuid.uuid4()}{ext}"

    storage_dir = Path(settings.screenshot_storage_path)
    storage_dir.mkdir(parents=True, exist_ok=True)

    dest_path = storage_dir / generated_name
    dest_path.write_bytes(data)

    screenshot = await ScreenshotRepository().create(
        db,
        test_failure_id=failure_id,
        original_filename=filename,
        content_type=content_type,
        storage_path=str(dest_path),
        file_size_bytes=len(data),
    )

    logger.info(
        "screenshot.saved",
        failure_id=str(failure_id),
        path=str(dest_path),
    )

    return screenshot
