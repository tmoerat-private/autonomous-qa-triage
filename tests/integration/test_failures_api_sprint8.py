"""Integration tests for Sprint 8 dashboard data-completeness additions.

Verifies that:
  - GET /api/v1/failures list items now carry category and confidence fields
  - GET /api/v1/failures/{id} detail response now carries commit_sha and repository

All tests hit a real PostgreSQL test database via the shared `client` and
`db_session` fixtures defined in tests/conftest.py.  Celery is mocked so no
real task dispatch occurs.
"""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.models.failure_classification import (
    FailureClassification,
)
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Module-level autouse — no real Celery calls in any test in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_celery_task():
    """Replace run_triage_pipeline.delay with a no-op for every test."""
    with patch("src.api.routes.failures.run_triage_pipeline") as mock_task:
        mock_task.delay = MagicMock(return_value=None)
        yield mock_task


# ---------------------------------------------------------------------------
# Private helpers — create ORM objects inside the test transaction
# ---------------------------------------------------------------------------


async def _make_event(
    db,
    commit_sha: str | None = "abc123",
    repository: str | None = "org/repo",
) -> PipelineEvent:
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id=f"run-{uuid.uuid4().hex[:8]}",
        repository=repository,
        branch="main",
        commit_sha=commit_sha,
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db.add(event)
    await db.flush()
    return event


async def _make_failure(db, event: PipelineEvent) -> TestFailure:
    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name=f"test_feature_{uuid.uuid4().hex[:6]}",
        error_message="AssertionError: expected True, got False",
        stack_trace="Traceback (most recent call last):\n  File 'test_foo.py', line 1\nAssertionError",
        status="triaged",
    )
    db.add(failure)
    await db.flush()
    return failure


async def _make_classification(
    db,
    failure: TestFailure,
    category: str = "product_bug",
    confidence: float = 0.9,
) -> FailureClassification:
    clf = FailureClassification(
        test_failure_id=failure.id,
        category=category,
        confidence=confidence,
        reasoning="Assertion failure in business logic",
        model_used="claude-sonnet-4-6",
    )
    db.add(clf)
    await db.flush()
    return clf


# ===========================================================================
# Test F — list failures includes category and confidence when classified
# ===========================================================================


async def test_list_failures_includes_category(client, db_session):
    """GET /api/v1/failures items carry category and confidence when a classification exists."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)
    await _make_classification(db_session, failure, category="product_bug", confidence=0.9)

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    items = response.json()["items"]
    target = next((i for i in items if i["id"] == str(failure.id)), None)
    assert target is not None, "seeded failure not found in list response"
    assert target["category"] == "product_bug"
    assert abs(target["confidence"] - 0.9) < 0.01


# ===========================================================================
# Test G — list failures category/confidence are None when no classification
# ===========================================================================


async def test_list_failures_category_none_when_no_classification(client, db_session):
    """GET /api/v1/failures items have category=None and confidence=None when unclassified."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)
    # No FailureClassification created

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    items = response.json()["items"]
    target = next((i for i in items if i["id"] == str(failure.id)), None)
    assert target is not None, "seeded failure not found in list response"
    assert target["category"] is None
    assert target["confidence"] is None


# ===========================================================================
# Test H — failure detail includes commit_sha and repository
# ===========================================================================


async def test_failure_detail_includes_commit_sha(client, db_session):
    """GET /api/v1/failures/{id} response includes commit_sha and repository from PipelineEvent."""
    event = await _make_event(db_session, commit_sha="deadbeef", repository="myorg/myrepo")
    failure = await _make_failure(db_session, event)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["commit_sha"] == "deadbeef"
    assert body["repository"] == "myorg/myrepo"


# ===========================================================================
# Test I — failure detail commit_sha is None when event has no commit
# ===========================================================================


async def test_failure_detail_commit_sha_none_without_commit(client, db_session):
    """GET /api/v1/failures/{id} returns commit_sha=None when PipelineEvent.commit_sha is NULL."""
    event = await _make_event(db_session, commit_sha=None, repository="org/repo")
    failure = await _make_failure(db_session, event)

    response = await client.get(f"/api/v1/failures/{failure.id}")

    assert response.status_code == 200
    body = response.json()
    # commit_sha should be None (or absent) when the column is NULL
    assert body.get("commit_sha") is None


# ===========================================================================
# Bonus — list response has both category fields when multiple failures mixed
# ===========================================================================


async def test_list_failures_category_stamped_per_item(client, db_session):
    """Each list item is independently stamped: classified items have a category, unclassified do not."""
    event = await _make_event(db_session)
    classified = await _make_failure(db_session, event)
    unclassified = await _make_failure(db_session, event)

    await _make_classification(db_session, classified, category="infrastructure_failure", confidence=0.85)

    response = await client.get("/api/v1/failures")

    assert response.status_code == 200
    items_by_id = {i["id"]: i for i in response.json()["items"]}

    classified_item = items_by_id.get(str(classified.id))
    unclassified_item = items_by_id.get(str(unclassified.id))

    assert classified_item is not None
    assert classified_item["category"] == "infrastructure_failure"
    assert abs(classified_item["confidence"] - 0.85) < 0.01

    assert unclassified_item is not None
    assert unclassified_item["category"] is None
    assert unclassified_item["confidence"] is None
