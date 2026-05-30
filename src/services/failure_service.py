from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from uuid import UUID

import structlog
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.error_signature import ErrorSignature
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
from src.models.triage_ticket import TriageTicket

logger = structlog.get_logger(__name__)


async def get_failures(
    db: AsyncSession,
    filters: dict,
    limit: int,
    offset: int,
) -> tuple[list[TestFailure], int]:
    """Return a paginated list of TestFailure records matching the given filters.

    Filters dict keys: status, category, repository, branch, date_from, date_to.
    repository and branch are matched against the linked PipelineEvent.
    category is matched against the linked FailureClassification.

    Returns a (rows, total_count) tuple so callers can build pagination envelopes.
    """
    conditions = []

    status: str | None = filters.get("status")
    if status is not None:
        conditions.append(TestFailure.status == status)

    date_from: datetime | None = filters.get("date_from")
    if date_from is not None:
        conditions.append(TestFailure.created_at >= date_from)

    date_to: datetime | None = filters.get("date_to")
    if date_to is not None:
        conditions.append(TestFailure.created_at <= date_to)

    # Filters that require joins
    repository: str | None = filters.get("repository")
    branch: str | None = filters.get("branch")
    category: str | None = filters.get("category")

    needs_event_join = repository is not None or branch is not None
    needs_classification_join = category is not None

    # Build the data query
    data_stmt = select(TestFailure)

    if needs_event_join:
        data_stmt = data_stmt.join(
            PipelineEvent,
            TestFailure.pipeline_event_id == PipelineEvent.id,
        )
        if repository is not None:
            conditions.append(PipelineEvent.repository == repository)
        if branch is not None:
            conditions.append(PipelineEvent.branch == branch)

    if needs_classification_join:
        data_stmt = data_stmt.join(
            FailureClassification,
            FailureClassification.test_failure_id == TestFailure.id,
        )
        conditions.append(FailureClassification.category == category)

    if conditions:
        data_stmt = data_stmt.where(and_(*conditions))

    data_stmt = (
        data_stmt.order_by(desc(TestFailure.created_at)).limit(limit).offset(offset)
    )

    # Count query — mirrors joins and conditions without ordering/pagination
    count_stmt = select(func.count()).select_from(TestFailure)

    if needs_event_join:
        count_stmt = count_stmt.join(
            PipelineEvent,
            TestFailure.pipeline_event_id == PipelineEvent.id,
        )
    if needs_classification_join:
        count_stmt = count_stmt.join(
            FailureClassification,
            FailureClassification.test_failure_id == TestFailure.id,
        )
    if conditions:
        count_stmt = count_stmt.where(and_(*conditions))

    data_result = await db.execute(data_stmt)
    rows = list(data_result.scalars().all())

    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar_one()

    logger.debug(
        "failure_service.get_failures",
        filters=filters,
        total=total,
        limit=limit,
        offset=offset,
    )
    return rows, total


async def get_failure_detail(db: AsyncSession, failure_id: UUID) -> dict | None:
    """Return a detail dict for one TestFailure, or None if not found.

    The dict contains:
      - failure: TestFailure ORM object
      - classification: FailureClassification | None
      - error_signature_hash: str | None (SHA-256 hex digest from ErrorSignature)
      - ticket: TriageTicket | None
    """
    stmt = select(TestFailure).where(TestFailure.id == failure_id)
    result = await db.execute(stmt)
    failure = result.scalar_one_or_none()

    if failure is None:
        return None

    classification_stmt = select(FailureClassification).where(
        FailureClassification.test_failure_id == failure_id
    )
    classification_result = await db.execute(classification_stmt)
    classification = classification_result.scalar_one_or_none()

    ticket_stmt = select(TriageTicket).where(
        TriageTicket.test_failure_id == failure_id
    )
    ticket_result = await db.execute(ticket_stmt)
    ticket = ticket_result.scalar_one_or_none()

    # Error signature — look up by computing the SHA-256 hash of the failure's
    # error_message and checking whether a matching ErrorSignature row exists.
    error_signature_hash: str | None = None
    if failure.error_message:
        candidate_hash = hashlib.sha256(failure.error_message.encode()).hexdigest()
        sig_stmt = select(ErrorSignature).where(
            ErrorSignature.signature_hash == candidate_hash
        )
        sig_result = await db.execute(sig_stmt)
        sig = sig_result.scalar_one_or_none()
        if sig is not None:
            error_signature_hash = sig.signature_hash

    logger.debug(
        "failure_service.get_failure_detail",
        failure_id=str(failure_id),
        has_classification=classification is not None,
        has_ticket=ticket is not None,
        has_signature=error_signature_hash is not None,
    )
    return {
        "failure": failure,
        "classification": classification,
        "error_signature_hash": error_signature_hash,
        "ticket": ticket,
    }


async def get_dashboard_summary(db: AsyncSession, period: str) -> dict:
    """Return aggregate counts for the dashboard summary panel.

    period must be one of "24h", "7d", "30d".
    Returns a dict with keys: period, by_status, by_category, total.
    """
    period_hours = {"24h": 24, "7d": 7 * 24, "30d": 30 * 24}
    hours = period_hours.get(period, 7 * 24)
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)

    status_stmt = (
        select(TestFailure.status, func.count().label("cnt"))
        .where(TestFailure.created_at >= since)
        .group_by(TestFailure.status)
    )
    status_result = await db.execute(status_stmt)
    by_status: dict[str, int] = {row.status: row.cnt for row in status_result}

    category_stmt = (
        select(FailureClassification.category, func.count().label("cnt"))
        .join(TestFailure, TestFailure.id == FailureClassification.test_failure_id)
        .where(TestFailure.created_at >= since)
        .group_by(FailureClassification.category)
    )
    category_result = await db.execute(category_stmt)
    by_category: dict[str, int] = {row.category: row.cnt for row in category_result}

    total = sum(by_status.values())

    logger.debug(
        "failure_service.get_dashboard_summary",
        period=period,
        total=total,
    )
    return {
        "period": period,
        "by_status": by_status,
        "by_category": by_category,
        "total": total,
    }


async def get_top_failing_tests(
    db: AsyncSession,
    days: int,
    limit: int = 10,
) -> list[dict]:
    """Return the tests with the highest failure count in the last `days` days.

    Returns a list of dicts with keys: test_name, count.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)

    stmt = (
        select(TestFailure.test_name, func.count().label("cnt"))
        .where(TestFailure.created_at >= since)
        .group_by(TestFailure.test_name)
        .order_by(desc(func.count()))
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = [{"test_name": row.test_name, "count": row.cnt} for row in result]

    logger.debug(
        "failure_service.get_top_failing_tests",
        days=days,
        limit=limit,
        result_count=len(rows),
    )
    return rows


async def get_daily_trends(db: AsyncSession, days: int) -> list[dict]:
    """Return daily failure counts for the last `days` days.

    Days with zero failures are filled in Python so the caller always receives a
    contiguous series. Returns a list of dicts with keys: date (YYYY-MM-DD),
    count.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(days=days)

    stmt = (
        select(
            func.date(TestFailure.created_at).label("day"),
            func.count().label("cnt"),
        )
        .where(TestFailure.created_at >= since)
        .group_by(func.date(TestFailure.created_at))
        .order_by(func.date(TestFailure.created_at))
    )
    result = await db.execute(stmt)
    db_rows: dict[str, int] = {str(row.day): row.cnt for row in result}

    # Build a contiguous date series, filling gaps with 0
    today = datetime.now(tz=timezone.utc).date()
    trends: list[dict] = []
    for i in range(days, 0, -1):
        day = today - timedelta(days=i - 1)
        day_str = day.strftime("%Y-%m-%d")
        trends.append({"date": day_str, "count": db_rows.get(day_str, 0)})

    logger.debug(
        "failure_service.get_daily_trends",
        days=days,
        db_rows_found=len(db_rows),
    )
    return trends
