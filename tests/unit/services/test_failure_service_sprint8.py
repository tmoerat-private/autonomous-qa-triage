"""Unit tests for Sprint 8 additions to failure_service.

Covers:
  - get_category_map() — batch classification lookup
  - get_failure_detail() — commit_sha / repository sourced from PipelineEvent

All tests hit a real PostgreSQL test database with transaction rollback
(see tests/conftest.py).  No external services are called.
"""
from __future__ import annotations

import uuid

from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
from src.services.failure_service import get_category_map, get_failure_detail

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
        test_name=f"test_something_{uuid.uuid4().hex[:6]}",
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
        reasoning="auto-generated",
        model_used="claude-sonnet-4-6",
    )
    db.add(clf)
    await db.flush()
    return clf


# ===========================================================================
# Test A — get_category_map returns correct mapping for multiple failures
# ===========================================================================


async def test_get_category_map_returns_correct_mapping(db_session):
    """get_category_map returns {failure_id: {category, confidence}} for each classified failure."""
    event = await _make_event(db_session)

    failure1 = await _make_failure(db_session, event)
    failure2 = await _make_failure(db_session, event)

    await _make_classification(db_session, failure1, category="product_bug", confidence=0.92)
    await _make_classification(db_session, failure2, category="flaky_test", confidence=0.75)

    result = await get_category_map(db_session, [failure1.id, failure2.id])

    assert failure1.id in result
    assert failure2.id in result

    assert result[failure1.id]["category"] == "product_bug"
    assert abs(result[failure1.id]["confidence"] - 0.92) < 0.001

    assert result[failure2.id]["category"] == "flaky_test"
    assert abs(result[failure2.id]["confidence"] - 0.75) < 0.001


# ===========================================================================
# Test B — get_category_map with an empty list
# ===========================================================================


async def test_get_category_map_empty_list(db_session):
    """get_category_map returns an empty dict when given an empty failure_ids list."""
    result = await get_category_map(db_session, [])

    assert result == {}


# ===========================================================================
# Test C — get_category_map omits failures with no classification
# ===========================================================================


async def test_get_category_map_unclassified_failure_absent(db_session):
    """get_category_map does not include failure IDs that have no classification row."""
    event = await _make_event(db_session)
    failure = await _make_failure(db_session, event)
    # Deliberately no FailureClassification created

    result = await get_category_map(db_session, [failure.id])

    assert failure.id not in result


# ===========================================================================
# Test D — get_failure_detail includes commit_sha and repository
# ===========================================================================


async def test_get_failure_detail_includes_commit_sha(db_session):
    """get_failure_detail returns commit_sha and repository sourced from the linked PipelineEvent."""
    event = await _make_event(db_session, commit_sha="abc123", repository="org/repo")
    failure = await _make_failure(db_session, event)

    detail = await get_failure_detail(db_session, failure.id)

    assert detail is not None
    assert detail["commit_sha"] == "abc123"
    assert detail["repository"] == "org/repo"


# ===========================================================================
# Test E — get_failure_detail commit_sha and repository reflect event values
# ===========================================================================


async def test_get_failure_detail_commit_sha_reflects_event_values(db_session):
    """commit_sha and repository in the detail dict exactly match the PipelineEvent columns.

    This verifies the correct event is joined when multiple events exist in the DB.
    """
    event_a = await _make_event(db_session, commit_sha="deadbeef", repository="myorg/service-a")
    event_b = await _make_event(db_session, commit_sha="cafebabe", repository="myorg/service-b")

    failure_a = await _make_failure(db_session, event_a)
    failure_b = await _make_failure(db_session, event_b)

    detail_a = await get_failure_detail(db_session, failure_a.id)
    detail_b = await get_failure_detail(db_session, failure_b.id)

    assert detail_a is not None
    assert detail_a["commit_sha"] == "deadbeef"
    assert detail_a["repository"] == "myorg/service-a"

    assert detail_b is not None
    assert detail_b["commit_sha"] == "cafebabe"
    assert detail_b["repository"] == "myorg/service-b"


# ===========================================================================
# Test — get_failure_detail returns None for unknown ID (regression guard)
# ===========================================================================


async def test_get_failure_detail_returns_none_for_unknown_id(db_session):
    """get_failure_detail returns None when no TestFailure with that ID exists."""
    unknown_id = uuid.uuid4()

    result = await get_failure_detail(db_session, unknown_id)

    assert result is None
