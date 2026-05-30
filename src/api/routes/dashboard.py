from __future__ import annotations

import structlog
from fastapi import APIRouter, Query

from src.api.dependencies import DbSession
from src.schemas.agent_schemas import DailyTrend, DashboardSummaryResponse, TopFailingTest
from src.services import failure_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_VALID_PERIODS = {"24h", "7d", "30d"}


@router.get("/summary", response_model=DashboardSummaryResponse)
async def dashboard_summary(
    db: DbSession,
    period: str = Query(default="7d"),
) -> DashboardSummaryResponse:
    """Return aggregate failure counts broken down by status and category.

    period must be one of "24h", "7d", "30d".  Defaults to "7d".
    """
    if period not in _VALID_PERIODS:
        period = "7d"

    summary = await failure_service.get_dashboard_summary(db, period)
    logger.info("dashboard.summary", period=period, total=summary["total"])
    return DashboardSummaryResponse(**summary)


@router.get("/top-failing", response_model=list[TopFailingTest])
async def top_failing_tests(
    db: DbSession,
    days: int = Query(default=7, ge=1, le=365),
) -> list[TopFailingTest]:
    """Return the tests with the most failures in the last `days` days."""
    rows = await failure_service.get_top_failing_tests(db, days=days)
    logger.info("dashboard.top_failing", days=days, result_count=len(rows))
    return [TopFailingTest(**row) for row in rows]


@router.get("/trends", response_model=list[DailyTrend])
async def daily_trends(
    db: DbSession,
    days: int = Query(default=30, ge=1, le=365),
) -> list[DailyTrend]:
    """Return daily failure counts for the last `days` days.

    Zero-count days are included so the caller always receives a contiguous
    date series suitable for chart rendering.
    """
    rows = await failure_service.get_daily_trends(db, days=days)
    logger.info("dashboard.trends", days=days, data_points=len(rows))
    return [DailyTrend(**row) for row in rows]
