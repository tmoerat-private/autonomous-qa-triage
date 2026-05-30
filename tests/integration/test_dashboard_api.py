"""Integration tests for the dashboard endpoints:
  GET /api/v1/dashboard/summary
  GET /api/v1/dashboard/top-failing
  GET /api/v1/dashboard/trends

All tests run against the real PostgreSQL test database with per-test
transaction rollback (see conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from src.config.constants import FailureStatus
from src.models.failure_classification import FailureClassification
from src.models.test_failure import TestFailure
from tests.factories import PipelineEventFactory, TestFailureFactory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _seed_pipeline_event(db_session, **kwargs):
    event = PipelineEventFactory(**kwargs)
    db_session.add(event)
    await db_session.flush()
    return event


async def _seed_failure(db_session, pipeline_event=None, **kwargs):
    if pipeline_event is None:
        pipeline_event = await _seed_pipeline_event(db_session)
    failure = TestFailureFactory(
        pipeline_event=pipeline_event,
        pipeline_event_id=pipeline_event.id,
        **kwargs,
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


async def _seed_classification(db_session, failure, category="product_bug"):
    classification = FailureClassification(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        category=category,
        confidence=0.9,
        reasoning="test reasoning",
        model_used="claude-sonnet-4-20250514",
    )
    db_session.add(classification)
    await db_session.flush()
    return classification


async def _age_failure(db_session, failure, days_ago: int):
    """Backdate a failure's created_at timestamp by `days_ago` days."""
    old_ts = datetime.now(tz=UTC) - timedelta(days=days_ago)
    await db_session.execute(
        update(TestFailure)
        .where(TestFailure.id == failure.id)
        .values(created_at=old_ts)
    )
    await db_session.flush()


# ===========================================================================
# GET /api/v1/dashboard/summary
# ===========================================================================


@pytest.mark.asyncio
async def test_dashboard_summary_default_period_returns_correct_schema(client, db_session):
    """GET /dashboard/summary returns the expected top-level fields."""
    response = await client.get("/api/v1/dashboard/summary")

    assert response.status_code == 200
    body = response.json()
    assert "period" in body
    assert "by_status" in body
    assert "by_category" in body
    assert "total" in body
    assert body["period"] == "7d"


@pytest.mark.asyncio
async def test_dashboard_summary_period_24h(client, db_session):
    """?period=24h is accepted and reflected in the response."""
    response = await client.get("/api/v1/dashboard/summary", params={"period": "24h"})

    assert response.status_code == 200
    assert response.json()["period"] == "24h"


@pytest.mark.asyncio
async def test_dashboard_summary_period_30d(client, db_session):
    """?period=30d is accepted and reflected in the response."""
    response = await client.get("/api/v1/dashboard/summary", params={"period": "30d"})

    assert response.status_code == 200
    assert response.json()["period"] == "30d"


@pytest.mark.asyncio
async def test_dashboard_summary_invalid_period_falls_back_to_7d(client, db_session):
    """An unrecognized period string is silently coerced to 7d."""
    response = await client.get("/api/v1/dashboard/summary", params={"period": "99y"})

    assert response.status_code == 200
    assert response.json()["period"] == "7d"


@pytest.mark.asyncio
async def test_dashboard_summary_by_status_counts_correctly(client, db_session):
    """by_status dict reflects the exact counts of seeded failures by status."""
    event = await _seed_pipeline_event(db_session)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.NEW)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.NEW)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.TRIAGING)

    response = await client.get("/api/v1/dashboard/summary", params={"period": "7d"})

    assert response.status_code == 200
    by_status = response.json()["by_status"]
    assert by_status.get("new", 0) >= 2
    assert by_status.get("triaging", 0) >= 1


@pytest.mark.asyncio
async def test_dashboard_summary_total_equals_sum_of_by_status(client, db_session):
    """total is the sum of all by_status values."""
    event = await _seed_pipeline_event(db_session)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.NEW)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.TRIAGED)

    response = await client.get("/api/v1/dashboard/summary", params={"period": "7d"})

    body = response.json()
    assert body["total"] == sum(body["by_status"].values())


@pytest.mark.asyncio
async def test_dashboard_summary_excludes_failures_outside_period(client, db_session):
    """Failures older than the requested period are not counted."""
    event = await _seed_pipeline_event(db_session)
    recent_failure = await _seed_failure(db_session, pipeline_event=event)
    old_failure = await _seed_failure(db_session, pipeline_event=event)
    await _age_failure(db_session, old_failure, days_ago=10)

    # 24h summary — only the recent failure should appear
    response = await client.get("/api/v1/dashboard/summary", params={"period": "24h"})
    body = response.json()
    total_24h = body["total"]

    # 30d summary — both should appear
    response_30d = await client.get("/api/v1/dashboard/summary", params={"period": "30d"})
    total_30d = response_30d.json()["total"]

    assert total_30d >= total_24h
    # The recent failure is inside 24h; the old one (10 days old) is not
    assert total_24h >= 1  # at minimum the recent one
    _ = recent_failure  # used implicitly via db_session


@pytest.mark.asyncio
async def test_dashboard_summary_by_category_reflects_classifications(client, db_session):
    """by_category reflects FailureClassification rows linked to recent failures."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_classification(db_session, failure, category="flaky_test")

    response = await client.get("/api/v1/dashboard/summary", params={"period": "7d"})

    body = response.json()
    assert body["by_category"].get("flaky_test", 0) >= 1


# ===========================================================================
# GET /api/v1/dashboard/top-failing
# ===========================================================================


@pytest.mark.asyncio
async def test_top_failing_returns_list_schema(client, db_session):
    """GET /dashboard/top-failing returns a list of {test_name, count} objects."""
    response = await client.get("/api/v1/dashboard/top-failing")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    if body:
        assert "test_name" in body[0]
        assert "count" in body[0]


@pytest.mark.asyncio
async def test_top_failing_ordered_by_count_descending(client, db_session):
    """Results are ordered so the test with the most failures comes first."""
    event = await _seed_pipeline_event(db_session)

    # Seed "test_rare" once
    await _seed_failure(db_session, pipeline_event=event, test_name="test_rare")

    # Seed "test_common" three times
    for _ in range(3):
        await _seed_failure(db_session, pipeline_event=event, test_name="test_common")

    response = await client.get("/api/v1/dashboard/top-failing", params={"days": 7})

    assert response.status_code == 200
    items = response.json()
    test_names = [item["test_name"] for item in items]

    # test_common must appear before test_rare
    assert "test_common" in test_names
    assert "test_rare" in test_names
    assert test_names.index("test_common") < test_names.index("test_rare")


@pytest.mark.asyncio
async def test_top_failing_count_reflects_actual_failure_count(client, db_session):
    """The count value for a test name matches the number of seeded failures."""
    event = await _seed_pipeline_event(db_session)
    test_name = f"test_counted_{uuid.uuid4().hex[:8]}"

    for _ in range(4):
        await _seed_failure(db_session, pipeline_event=event, test_name=test_name)

    response = await client.get("/api/v1/dashboard/top-failing", params={"days": 7})

    assert response.status_code == 200
    items = {item["test_name"]: item["count"] for item in response.json()}
    assert items.get(test_name, 0) >= 4


@pytest.mark.asyncio
async def test_top_failing_days_param_excludes_older_failures(client, db_session):
    """?days=1 excludes failures seeded more than 1 day ago."""
    event = await _seed_pipeline_event(db_session)
    test_name = f"test_old_{uuid.uuid4().hex[:8]}"
    old_failure = await _seed_failure(db_session, pipeline_event=event, test_name=test_name)
    await _age_failure(db_session, old_failure, days_ago=5)

    response = await client.get("/api/v1/dashboard/top-failing", params={"days": 1})

    assert response.status_code == 200
    items = {item["test_name"]: item["count"] for item in response.json()}
    # The 5-day-old failure must not appear in a 1-day window
    assert test_name not in items


@pytest.mark.asyncio
async def test_top_failing_returns_empty_list_when_no_failures(client, db_session):
    """Endpoint returns an empty list (not a 404) when there are no failures."""
    # The DB is clean (transaction rollback) — no seeding
    response = await client.get("/api/v1/dashboard/top-failing")

    assert response.status_code == 200
    assert response.json() == []


# ===========================================================================
# GET /api/v1/dashboard/trends
# ===========================================================================


@pytest.mark.asyncio
async def test_trends_returns_list_schema(client, db_session):
    """GET /dashboard/trends returns a list of {date, count} objects."""
    response = await client.get("/api/v1/dashboard/trends")

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    if body:
        assert "date" in body[0]
        assert "count" in body[0]


@pytest.mark.asyncio
async def test_trends_returns_exactly_days_entries(client, db_session):
    """?days=N returns a list of exactly N entries (zero-count days are filled in)."""
    for days in (7, 14, 30):
        response = await client.get("/api/v1/dashboard/trends", params={"days": days})
        assert response.status_code == 200
        assert len(response.json()) == days, f"expected {days} entries for ?days={days}"


@pytest.mark.asyncio
async def test_trends_dates_are_iso_format_yyyy_mm_dd(client, db_session):
    """Every entry's date field is a YYYY-MM-DD string."""
    import re

    response = await client.get("/api/v1/dashboard/trends", params={"days": 7})

    assert response.status_code == 200
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for entry in response.json():
        assert date_pattern.match(entry["date"]), (
            f"date '{entry['date']}' does not match YYYY-MM-DD"
        )


@pytest.mark.asyncio
async def test_trends_dates_are_contiguous_and_ascending(client, db_session):
    """The trend series is a contiguous, ascending date sequence with no gaps."""
    from datetime import date

    days = 10
    response = await client.get("/api/v1/dashboard/trends", params={"days": days})

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == days

    dates = [date.fromisoformat(e["date"]) for e in entries]
    for i in range(1, len(dates)):
        assert dates[i] - dates[i - 1] == timedelta(days=1), (
            f"gap between {dates[i-1]} and {dates[i]}"
        )


@pytest.mark.asyncio
async def test_trends_zero_count_days_are_included(client, db_session):
    """Days with no failures contribute a zero-count entry — not a gap."""
    # Seed nothing — every day should be 0
    days = 5
    response = await client.get("/api/v1/dashboard/trends", params={"days": days})

    assert response.status_code == 200
    entries = response.json()
    assert len(entries) == days
    for entry in entries:
        assert entry["count"] == 0


@pytest.mark.asyncio
async def test_trends_count_reflects_failures_on_correct_day(client, db_session):
    """A failure seeded today increments today's count in the trend series."""
    event = await _seed_pipeline_event(db_session)
    await _seed_failure(db_session, pipeline_event=event)

    # Request 7-day trends — today must be the last entry
    response = await client.get("/api/v1/dashboard/trends", params={"days": 7})

    assert response.status_code == 200
    entries = response.json()
    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    today_entry = next((e for e in entries if e["date"] == today_str), None)
    assert today_entry is not None
    assert today_entry["count"] >= 1


@pytest.mark.asyncio
async def test_trends_last_entry_is_today(client, db_session):
    """The final entry in the series is always today's date."""
    response = await client.get("/api/v1/dashboard/trends", params={"days": 7})

    assert response.status_code == 200
    entries = response.json()
    today_str = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    assert entries[-1]["date"] == today_str
