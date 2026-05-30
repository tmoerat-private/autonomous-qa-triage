"""Integration tests for GET /api/v1/failures, GET /api/v1/failures/{id},
and POST /api/v1/failures/{id}/retriage endpoints.

All tests run against a real PostgreSQL test database with transaction rollback
per test (see conftest.py).  Celery is always mocked — .delay() is never called
for real.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.config.constants import FailureCategory, FailureStatus
from src.models.failure_classification import FailureClassification
from src.models.triage_ticket import TriageTicket
from tests.factories import PipelineEventFactory, TestFailureFactory


# ---------------------------------------------------------------------------
# Module-level autouse — no real Celery calls in any test in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_celery_task():
    """Replace run_triage_pipeline.delay with a no-op for every test."""
    with patch("src.api.routes.failures.run_triage_pipeline") as mock_task:
        mock_task.delay = MagicMock(return_value=None)
        yield mock_task


# ---------------------------------------------------------------------------
# Helpers — seed objects into the shared db_session
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


async def _seed_classification(db_session, failure, category="product_bug", confidence=0.9):
    classification = FailureClassification(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        category=category,
        confidence=confidence,
        reasoning="Assertion failure in business logic",
        model_used="claude-sonnet-4-20250514",
    )
    db_session.add(classification)
    await db_session.flush()
    return classification


async def _seed_ticket(db_session, failure, provider="jira", external_ticket_id="PROJ-42"):
    ticket = TriageTicket(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        provider=provider,
        external_ticket_id=external_ticket_id,
        external_url="https://jira.example.com/browse/PROJ-42",
        title="Test failure: " + failure.test_name,
        priority="high",
        status="open",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


# ===========================================================================
# GET /api/v1/failures — no filters
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_no_filters_returns_paginated_shape(client, db_session):
    """GET /api/v1/failures returns PaginatedFailuresResponse with correct envelope."""
    event = await _seed_pipeline_event(db_session)
    await _seed_failure(db_session, pipeline_event=event)
    await _seed_failure(db_session, pipeline_event=event)

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert body["total"] >= 2
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_list_failures_item_has_required_fields(client, db_session):
    """Each item in the list response contains all FailureListItem fields."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) >= 1

    item = next(i for i in items if i["id"] == str(failure.id))
    assert item["test_name"] == failure.test_name
    assert item["status"] == str(failure.status)
    assert "pipeline_event_id" in item
    assert "created_at" in item
    assert "updated_at" in item


# ===========================================================================
# GET /api/v1/failures — status filter
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_status_filter_returns_only_matching(client, db_session):
    """?status=triaging returns only failures whose status matches."""
    event = await _seed_pipeline_event(db_session)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.NEW)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.TRIAGING)
    await _seed_failure(db_session, pipeline_event=event, status=FailureStatus.TRIAGING)

    response = await client.get("/api/v1/failures", params={"status": "triaging"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] >= 2
    for item in body["items"]:
        assert item["status"] == "triaging"


# ===========================================================================
# GET /api/v1/failures — category filter
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_category_filter_requires_classification_join(client, db_session):
    """?category=product_bug returns only failures that have a matching classification."""
    event = await _seed_pipeline_event(db_session)
    failure_with_class = await _seed_failure(db_session, pipeline_event=event)
    failure_no_class = await _seed_failure(db_session, pipeline_event=event)  # noqa: F841
    await _seed_classification(db_session, failure_with_class, category="product_bug")

    response = await client.get("/api/v1/failures", params={"category": "product_bug"})

    assert response.status_code == 200
    body = response.json()
    # Only the classified failure should appear
    returned_ids = {item["id"] for item in body["items"]}
    assert str(failure_with_class.id) in returned_ids
    # The unclassified failure must not appear when filtering by category
    assert str(failure_no_class.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_failures_category_filter_excludes_different_category(client, db_session):
    """?category=flaky_test does not return failures classified as product_bug."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_classification(db_session, failure, category="product_bug")

    response = await client.get("/api/v1/failures", params={"category": "flaky_test"})

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(failure.id) not in returned_ids


# ===========================================================================
# GET /api/v1/failures — repository and branch filters
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_repository_filter_matches_pipeline_event(client, db_session):
    """?repository=org/repo returns only failures from that repository."""
    event_a = await _seed_pipeline_event(db_session, repository="org/service-a")
    event_b = await _seed_pipeline_event(db_session, repository="org/service-b")
    failure_a = await _seed_failure(db_session, pipeline_event=event_a)
    failure_b = await _seed_failure(db_session, pipeline_event=event_b)  # noqa: F841

    response = await client.get("/api/v1/failures", params={"repository": "org/service-a"})

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(failure_a.id) in returned_ids
    assert str(failure_b.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_failures_branch_filter_matches_pipeline_event(client, db_session):
    """?branch=feature/x returns only failures from that branch."""
    event_main = await _seed_pipeline_event(db_session, branch="main")
    event_feat = await _seed_pipeline_event(db_session, branch="feature/x")
    failure_main = await _seed_failure(db_session, pipeline_event=event_main)  # noqa: F841
    failure_feat = await _seed_failure(db_session, pipeline_event=event_feat)

    response = await client.get("/api/v1/failures", params={"branch": "feature/x"})

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(failure_feat.id) in returned_ids
    assert str(failure_main.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_failures_combined_repository_and_branch_filter(client, db_session):
    """?repository=X&branch=Y applies both filters together (AND semantics)."""
    event_match = await _seed_pipeline_event(
        db_session, repository="org/repo", branch="main"
    )
    event_wrong_branch = await _seed_pipeline_event(
        db_session, repository="org/repo", branch="develop"
    )
    failure_match = await _seed_failure(db_session, pipeline_event=event_match)
    failure_wrong = await _seed_failure(db_session, pipeline_event=event_wrong_branch)  # noqa: F841

    response = await client.get(
        "/api/v1/failures", params={"repository": "org/repo", "branch": "main"}
    )

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(failure_match.id) in returned_ids
    assert str(failure_wrong.id) not in returned_ids


# ===========================================================================
# GET /api/v1/failures — date range filters
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_date_from_filter_excludes_older_records(client, db_session):
    """?date_from=T excludes failures created before T."""
    from sqlalchemy import update
    from src.models.test_failure import TestFailure

    event = await _seed_pipeline_event(db_session)
    old_failure = await _seed_failure(db_session, pipeline_event=event)
    new_failure = await _seed_failure(db_session, pipeline_event=event)

    # Force the old failure's created_at to 10 days ago via a direct update
    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=10)
    await db_session.execute(
        update(TestFailure)
        .where(TestFailure.id == old_failure.id)
        .values(created_at=old_ts)
    )
    await db_session.flush()

    # Filter from 2 days ago — should exclude the 10-day-old record
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=2)).isoformat()
    response = await client.get("/api/v1/failures", params={"date_from": cutoff})

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(new_failure.id) in returned_ids
    assert str(old_failure.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_failures_date_to_filter_excludes_newer_records(client, db_session):
    """?date_to=T excludes failures created after T."""
    from sqlalchemy import update
    from src.models.test_failure import TestFailure

    event = await _seed_pipeline_event(db_session)
    old_failure = await _seed_failure(db_session, pipeline_event=event)
    new_failure = await _seed_failure(db_session, pipeline_event=event)  # noqa: F841

    old_ts = datetime.now(tz=timezone.utc) - timedelta(days=10)
    await db_session.execute(
        update(TestFailure)
        .where(TestFailure.id == old_failure.id)
        .values(created_at=old_ts)
    )
    await db_session.flush()

    # Filter up to 5 days ago — only the old record fits
    cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=5)).isoformat()
    response = await client.get("/api/v1/failures", params={"date_to": cutoff})

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(old_failure.id) in returned_ids
    assert str(new_failure.id) not in returned_ids


# ===========================================================================
# GET /api/v1/failures — pagination
# ===========================================================================


@pytest.mark.asyncio
async def test_list_failures_limit_restricts_returned_items(client, db_session):
    """?limit=2 returns at most 2 items even when more exist."""
    event = await _seed_pipeline_event(db_session)
    for _ in range(5):
        await _seed_failure(db_session, pipeline_event=event)

    response = await client.get("/api/v1/failures", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["total"] >= 5


@pytest.mark.asyncio
async def test_list_failures_offset_skips_records(client, db_session):
    """?limit=2&offset=2 returns the third and fourth records."""
    event = await _seed_pipeline_event(db_session)
    for _ in range(4):
        await _seed_failure(db_session, pipeline_event=event)

    page1 = (await client.get("/api/v1/failures", params={"limit": 2, "offset": 0})).json()
    page2 = (await client.get("/api/v1/failures", params={"limit": 2, "offset": 2})).json()

    ids_page1 = {item["id"] for item in page1["items"]}
    ids_page2 = {item["id"] for item in page2["items"]}
    # The two pages must not overlap
    assert ids_page1.isdisjoint(ids_page2)
    assert page2["offset"] == 2


# ===========================================================================
# GET /api/v1/failures/{failure_id} — detail
# ===========================================================================


@pytest.mark.asyncio
async def test_get_failure_detail_returns_full_record(client, db_session):
    """GET /api/v1/failures/{id} returns the failure's full detail payload."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(failure.id)
    assert body["test_name"] == failure.test_name
    assert body["status"] == str(failure.status)
    assert "pipeline_event_id" in body
    assert "created_at" in body
    assert "updated_at" in body


@pytest.mark.asyncio
async def test_get_failure_detail_includes_classification_when_present(client, db_session):
    """Failure detail response nests the classification object when one exists."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_classification(db_session, failure, category="flaky_test", confidence=0.85)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] is not None
    assert body["classification"]["category"] == "flaky_test"
    assert body["classification"]["confidence"] == pytest.approx(0.85)


@pytest.mark.asyncio
async def test_get_failure_detail_classification_is_none_when_absent(client, db_session):
    """classification field is null when no classification row exists."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["classification"] is None


@pytest.mark.asyncio
async def test_get_failure_detail_includes_ticket_when_present(client, db_session):
    """Failure detail response nests the ticket object when one exists."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)
    await _seed_ticket(db_session, failure, provider="jira", external_ticket_id="PROJ-99")

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["ticket"] is not None
    assert body["ticket"]["provider"] == "jira"
    assert body["ticket"]["external_ticket_id"] == "PROJ-99"


@pytest.mark.asyncio
async def test_get_failure_detail_ticket_is_none_when_absent(client, db_session):
    """ticket field is null when no triage ticket exists."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    assert response.json()["ticket"] is None


@pytest.mark.asyncio
async def test_get_failure_detail_returns_404_for_unknown_id(client, db_session):
    """GET /api/v1/failures/{unknown_uuid} returns 404."""
    unknown_id = uuid.uuid4()

    response = await client.get(f"/api/v1/failures/{unknown_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"


# ===========================================================================
# POST /api/v1/failures/{failure_id}/retriage
# ===========================================================================


@pytest.mark.asyncio
async def test_retriage_returns_202_and_enqueues_celery_task(client, db_session, mock_celery_task):
    """POST /retriage returns 202 and calls run_triage_pipeline.delay."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    response = await client.post(f"/api/v1/failures/{failure.id}/retriage")

    assert response.status_code == 202
    body = response.json()
    assert body["message"] == "triage enqueued"
    assert body["failure_id"] == str(failure.id)
    mock_celery_task.delay.assert_called_once_with(
        pipeline_event_id=str(failure.pipeline_event_id)
    )


@pytest.mark.asyncio
async def test_retriage_returns_404_for_unknown_failure_id(client, db_session, mock_celery_task):
    """POST /retriage returns 404 when the failure does not exist."""
    unknown_id = uuid.uuid4()

    response = await client.post(f"/api/v1/failures/{unknown_id}/retriage")

    assert response.status_code == 404
    assert response.json()["detail"] == "failure not found"
    mock_celery_task.delay.assert_not_called()


@pytest.mark.asyncio
async def test_retriage_passes_correct_pipeline_event_id_to_task(client, db_session, mock_celery_task):
    """The pipeline_event_id forwarded to Celery matches the failure's FK."""
    event = await _seed_pipeline_event(db_session)
    failure = await _seed_failure(db_session, pipeline_event=event)

    await client.post(f"/api/v1/failures/{failure.id}/retriage")

    call_kwargs = mock_celery_task.delay.call_args.kwargs
    assert call_kwargs["pipeline_event_id"] == str(failure.pipeline_event_id)
