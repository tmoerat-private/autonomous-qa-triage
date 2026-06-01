from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.test_failure import TestFailure
from src.models.test_screenshot import TestScreenshot

logger = structlog.get_logger(__name__)


class ScreenshotRepository:
    """Data access layer for TestScreenshot records."""

    async def create(self, session: AsyncSession, **kwargs) -> TestScreenshot:
        """Create and persist a TestScreenshot.

        The caller controls the transaction; this method only flushes.
        """
        screenshot = TestScreenshot(**kwargs)
        session.add(screenshot)
        await session.flush()
        logger.info(
            "screenshot.created",
            screenshot_id=str(screenshot.id),
            test_failure_id=str(screenshot.test_failure_id),
            storage_path=screenshot.storage_path,
        )
        return screenshot

    async def get_by_id(
        self, session: AsyncSession, id: UUID
    ) -> TestScreenshot | None:
        """Return a TestScreenshot by primary key, or None if not found."""
        stmt = select(TestScreenshot).where(TestScreenshot.id == id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_failure_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> list[TestScreenshot]:
        """Return all screenshots for a test failure, ordered by created_at ASC."""
        stmt = (
            select(TestScreenshot)
            .where(TestScreenshot.test_failure_id == failure_id)
            .order_by(TestScreenshot.created_at.asc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_latest_baseline(
        self, session: AsyncSession, test_name: str
    ) -> TestScreenshot | None:
        """Return the most recently created screenshot for a given test name.

        Used to find a "known good" baseline screenshot by joining to the
        test_failures table and filtering on test_name.
        """
        stmt = (
            select(TestScreenshot)
            .join(TestFailure, TestScreenshot.test_failure_id == TestFailure.id)
            .where(TestFailure.test_name == test_name)
            .order_by(TestScreenshot.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()
