from __future__ import annotations

from datetime import datetime, timedelta, timezone

import structlog
from sqlalchemy import and_, distinct, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

logger = structlog.get_logger(__name__)


class FlakynessRepository:
    """Data access layer for flaky test detection queries.

    Operates over the existing test_failures and pipeline_events tables —
    no separate storage is required.
    """

    async def get_failure_count_for_test(
        self,
        session: AsyncSession,
        test_name: str,
        repository: str | None,
        lookback_days: int,
    ) -> int:
        """Count distinct pipeline_events in which test_name failed within lookback window.

        Joins test_failures to pipeline_events and counts distinct pipeline event IDs.
        If repository is provided it is used as an additional filter.
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        conditions = [
            TestFailure.test_name == test_name,
            TestFailure.created_at >= since,
        ]
        if repository is not None:
            conditions.append(PipelineEvent.repository == repository)

        stmt = (
            select(func.count(distinct(PipelineEvent.id)))
            .select_from(TestFailure)
            .join(PipelineEvent, TestFailure.pipeline_event_id == PipelineEvent.id)
            .where(and_(*conditions))
        )
        result = await session.execute(stmt)
        count: int = result.scalar_one()
        logger.debug(
            "flakiness.failure_count_for_test",
            test_name=test_name,
            repository=repository,
            lookback_days=lookback_days,
            count=count,
        )
        return count

    async def get_total_pipeline_runs(
        self,
        session: AsyncSession,
        repository: str | None,
        lookback_days: int,
    ) -> int:
        """Count distinct pipeline_events within the lookback window.

        If repository is provided it is used as an additional filter.
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        conditions: list = [PipelineEvent.created_at >= since]
        if repository is not None:
            conditions.append(PipelineEvent.repository == repository)

        stmt = select(func.count(distinct(PipelineEvent.id))).where(and_(*conditions))
        result = await session.execute(stmt)
        count: int = result.scalar_one()
        logger.debug(
            "flakiness.total_pipeline_runs",
            repository=repository,
            lookback_days=lookback_days,
            count=count,
        )
        return count

    async def get_retry_rate_for_test(
        self,
        session: AsyncSession,
        test_name: str,
        repository: str | None,
        lookback_days: int,
    ) -> float:
        """Return the fraction of this test's failures that had retry_count > 0.

        Returns 0.0 if no failures are found in the window.
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        base_conditions = [
            TestFailure.test_name == test_name,
            TestFailure.created_at >= since,
        ]
        if repository is not None:
            base_conditions.append(PipelineEvent.repository == repository)

        # Total failure count for this test in the window
        total_stmt = (
            select(func.count(TestFailure.id))
            .select_from(TestFailure)
            .join(PipelineEvent, TestFailure.pipeline_event_id == PipelineEvent.id)
            .where(and_(*base_conditions))
        )
        total_result = await session.execute(total_stmt)
        total: int = total_result.scalar_one()

        if total == 0:
            return 0.0

        # Failures where retry_count > 0
        retried_stmt = (
            select(func.count(TestFailure.id))
            .select_from(TestFailure)
            .join(PipelineEvent, TestFailure.pipeline_event_id == PipelineEvent.id)
            .where(and_(*base_conditions, TestFailure.retry_count > 0))
        )
        retried_result = await session.execute(retried_stmt)
        retried: int = retried_result.scalar_one()

        rate = retried / total
        logger.debug(
            "flakiness.retry_rate_for_test",
            test_name=test_name,
            repository=repository,
            lookback_days=lookback_days,
            total=total,
            retried=retried,
            rate=rate,
        )
        return rate

    async def get_failure_history(
        self,
        session: AsyncSession,
        test_name: str,
        repository: str | None,
        lookback_days: int,
        limit: int = 50,
    ) -> list[dict]:
        """Return the most recent failures for this test name, newest first.

        Each dict has keys:
          failure_id (str UUID), pipeline_event_id (str UUID),
          repository (str | None), created_at (datetime), retry_count (int).
        """
        since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

        conditions = [
            TestFailure.test_name == test_name,
            TestFailure.created_at >= since,
        ]
        if repository is not None:
            conditions.append(PipelineEvent.repository == repository)

        stmt = (
            select(
                TestFailure.id,
                TestFailure.pipeline_event_id,
                PipelineEvent.repository,
                TestFailure.created_at,
                TestFailure.retry_count,
            )
            .select_from(TestFailure)
            .join(PipelineEvent, TestFailure.pipeline_event_id == PipelineEvent.id)
            .where(and_(*conditions))
            .order_by(TestFailure.created_at.desc())
            .limit(limit)
        )
        result = await session.execute(stmt)
        rows = result.all()

        history = [
            {
                "failure_id": str(row.id),
                "pipeline_event_id": str(row.pipeline_event_id),
                "repository": row.repository,
                "created_at": row.created_at,
                "retry_count": row.retry_count,
            }
            for row in rows
        ]
        logger.debug(
            "flakiness.failure_history",
            test_name=test_name,
            repository=repository,
            lookback_days=lookback_days,
            returned=len(history),
        )
        return history
