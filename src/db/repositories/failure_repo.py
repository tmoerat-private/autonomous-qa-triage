from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.test_failure import TestFailure

logger = structlog.get_logger(__name__)


class FailureRepository:
    """Data access layer for TestFailure records."""

    async def create(
        self,
        session: AsyncSession,
        pipeline_event_id: UUID,
        test_name: str,
        test_suite: str | None = None,
        test_file: str | None = None,
        error_message: str | None = None,
        stack_trace: str | None = None,
        duration_ms: int | None = None,
    ) -> TestFailure:
        """Create and persist a single TestFailure with status='new'.

        The caller controls the transaction; this method only flushes.
        """
        failure = TestFailure(
            pipeline_event_id=pipeline_event_id,
            test_name=test_name,
            test_suite=test_suite,
            test_file=test_file,
            error_message=error_message,
            stack_trace=stack_trace,
            duration_ms=duration_ms,
            status="new",
        )
        session.add(failure)
        await session.flush()
        logger.info(
            "test_failure.created",
            failure_id=str(failure.id),
            pipeline_event_id=str(pipeline_event_id),
            test_name=test_name,
        )
        return failure

    async def create_many(
        self,
        session: AsyncSession,
        pipeline_event_id: UUID,
        failures: list[dict],
    ) -> list[TestFailure]:
        """Bulk-create TestFailure records from a list of dicts.

        Expected dict keys: test_name (required), test_suite, test_file,
        error_message, stack_trace, duration_ms (all optional except test_name).
        Adds all records and flushes once.
        """
        instances: list[TestFailure] = [
            TestFailure(
                pipeline_event_id=pipeline_event_id,
                test_name=f["test_name"],
                test_suite=f.get("test_suite"),
                test_file=f.get("test_file"),
                error_message=f.get("error_message"),
                stack_trace=f.get("stack_trace"),
                duration_ms=f.get("duration_ms"),
                status="new",
            )
            for f in failures
        ]
        session.add_all(instances)
        await session.flush()
        logger.info(
            "test_failure.bulk_created",
            pipeline_event_id=str(pipeline_event_id),
            count=len(instances),
        )
        return instances

    async def get_by_id(
        self, session: AsyncSession, failure_id: UUID
    ) -> TestFailure | None:
        """Return a TestFailure by primary key, or None if not found."""
        stmt = select(TestFailure).where(TestFailure.id == failure_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_pipeline_event(
        self, session: AsyncSession, pipeline_event_id: UUID
    ) -> list[TestFailure]:
        """Return all TestFailures for a pipeline event ordered by created_at."""
        stmt = (
            select(TestFailure)
            .where(TestFailure.pipeline_event_id == pipeline_event_id)
            .order_by(TestFailure.created_at)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def update_status(
        self, session: AsyncSession, failure_id: UUID, status: str
    ) -> TestFailure:
        """Update the status of a TestFailure in place.

        Raises ValueError if the failure does not exist.
        """
        failure = await self.get_by_id(session, failure_id)
        if failure is None:
            raise ValueError(f"TestFailure not found: {failure_id}")
        failure.status = status
        await session.flush()
        logger.info(
            "test_failure.status_updated",
            failure_id=str(failure_id),
            status=status,
        )
        return failure

    async def list_by_status(
        self,
        session: AsyncSession,
        status: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TestFailure]:
        """Return paginated TestFailures filtered by status."""
        stmt = (
            select(TestFailure)
            .where(TestFailure.status == status)
            .order_by(TestFailure.created_at)
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())
