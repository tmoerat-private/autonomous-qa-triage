"""Integration tests for the agent-runs endpoints:
  GET /api/v1/agent-runs
  GET /api/v1/agent-runs/{run_id}

All tests run against the real PostgreSQL test database with per-test
transaction rollback (see conftest.py).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from src.config.constants import AgentRunStatus
from src.models.agent_run import AgentRun
from tests.factories import PipelineEventFactory, TestFailureFactory

# ---------------------------------------------------------------------------
# Helpers — build and persist domain objects
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


async def _seed_agent_run(
    db_session,
    failure=None,
    agent_name: str = "failure_classifier",
    status: str = AgentRunStatus.COMPLETED,
    input_summary: str | None = "error_message=AssertionError",
    output_summary: str | None = "category=product_bug",
    duration_ms: int | None = 350,
    tokens_used: int | None = 820,
    **kwargs,
) -> AgentRun:
    if failure is None:
        failure = await _seed_failure(db_session)
    run = AgentRun(
        id=uuid.uuid4(),
        test_failure_id=failure.id,
        agent_name=agent_name,
        status=status,
        input_summary=input_summary,
        output_summary=output_summary,
        duration_ms=duration_ms,
        tokens_used=tokens_used,
        started_at=datetime.now(tz=UTC),
        **kwargs,
    )
    db_session.add(run)
    await db_session.flush()
    return run


# ===========================================================================
# GET /api/v1/agent-runs — list
# ===========================================================================


@pytest.mark.asyncio
async def test_list_agent_runs_returns_paginated_envelope(client, db_session):
    """GET /agent-runs returns a PaginatedAgentRunsResponse envelope."""
    await _seed_agent_run(db_session)

    response = await client.get("/api/v1/agent-runs")

    assert response.status_code == 200
    body = response.json()
    assert "items" in body
    assert "total" in body
    assert "limit" in body
    assert "offset" in body
    assert isinstance(body["items"], list)


@pytest.mark.asyncio
async def test_list_agent_runs_item_has_required_fields(client, db_session):
    """Each agent-run item exposes all AgentRunItem schema fields."""
    run = await _seed_agent_run(db_session)

    response = await client.get("/api/v1/agent-runs")

    assert response.status_code == 200
    items = response.json()["items"]
    item = next(i for i in items if i["id"] == str(run.id))
    assert item["agent_name"] == run.agent_name
    assert item["status"] == str(run.status)
    assert "test_failure_id" in item
    assert "started_at" in item
    assert "created_at" in item
    assert "updated_at" in item


@pytest.mark.asyncio
async def test_list_agent_runs_empty_db_returns_zero_total(client, db_session):
    """When no agent runs exist the response has total=0 and items=[]."""
    response = await client.get("/api/v1/agent-runs")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# ===========================================================================
# GET /api/v1/agent-runs — filtered by test_failure_id
# ===========================================================================


@pytest.mark.asyncio
async def test_list_agent_runs_filter_by_test_failure_id(client, db_session):
    """?test_failure_id=X returns only runs linked to that failure."""
    failure_a = await _seed_failure(db_session)
    failure_b = await _seed_failure(db_session)
    run_a = await _seed_agent_run(db_session, failure=failure_a)
    run_b = await _seed_agent_run(db_session, failure=failure_b)

    response = await client.get(
        "/api/v1/agent-runs", params={"test_failure_id": str(failure_a.id)}
    )

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(run_a.id) in returned_ids
    assert str(run_b.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_agent_runs_filter_by_test_failure_id_total_is_correct(client, db_session):
    """Total reflects the filtered count, not the full table count."""
    failure_a = await _seed_failure(db_session)
    failure_b = await _seed_failure(db_session)
    await _seed_agent_run(db_session, failure=failure_a)
    await _seed_agent_run(db_session, failure=failure_a)
    await _seed_agent_run(db_session, failure=failure_b)

    response = await client.get(
        "/api/v1/agent-runs", params={"test_failure_id": str(failure_a.id)}
    )

    assert response.status_code == 200
    assert response.json()["total"] == 2


@pytest.mark.asyncio
async def test_list_agent_runs_unknown_failure_id_returns_empty(client, db_session):
    """?test_failure_id=<non-existent-uuid> returns total=0, items=[]."""
    await _seed_agent_run(db_session)

    response = await client.get(
        "/api/v1/agent-runs", params={"test_failure_id": str(uuid.uuid4())}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


# ===========================================================================
# GET /api/v1/agent-runs — filtered by agent_name
# ===========================================================================


@pytest.mark.asyncio
async def test_list_agent_runs_filter_by_agent_name(client, db_session):
    """?agent_name=X returns only runs with that agent_name."""
    failure = await _seed_failure(db_session)
    classifier_run = await _seed_agent_run(
        db_session, failure=failure, agent_name="failure_classifier"
    )
    analyzer_run = await _seed_agent_run(
        db_session, failure=failure, agent_name="log_analyzer"
    )

    response = await client.get(
        "/api/v1/agent-runs", params={"agent_name": "failure_classifier"}
    )

    assert response.status_code == 200
    body = response.json()
    returned_ids = {item["id"] for item in body["items"]}
    assert str(classifier_run.id) in returned_ids
    assert str(analyzer_run.id) not in returned_ids


@pytest.mark.asyncio
async def test_list_agent_runs_combined_filters(client, db_session):
    """?test_failure_id=X&agent_name=Y applies both filters (AND semantics)."""
    failure_a = await _seed_failure(db_session)
    failure_b = await _seed_failure(db_session)

    run_a_classifier = await _seed_agent_run(
        db_session, failure=failure_a, agent_name="failure_classifier"
    )
    run_a_analyzer = await _seed_agent_run(  # noqa: F841
        db_session, failure=failure_a, agent_name="log_analyzer"
    )
    run_b_classifier = await _seed_agent_run(  # noqa: F841
        db_session, failure=failure_b, agent_name="failure_classifier"
    )

    response = await client.get(
        "/api/v1/agent-runs",
        params={
            "test_failure_id": str(failure_a.id),
            "agent_name": "failure_classifier",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["id"] == str(run_a_classifier.id)


# ===========================================================================
# GET /api/v1/agent-runs — pagination
# ===========================================================================


@pytest.mark.asyncio
async def test_list_agent_runs_limit_restricts_items_returned(client, db_session):
    """?limit=2 returns at most 2 items when more exist."""
    failure = await _seed_failure(db_session)
    for _ in range(5):
        await _seed_agent_run(db_session, failure=failure)

    response = await client.get("/api/v1/agent-runs", params={"limit": 2})

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 2
    assert body["limit"] == 2
    assert body["total"] >= 5


@pytest.mark.asyncio
async def test_list_agent_runs_offset_skips_records(client, db_session):
    """?limit=2&offset=2 returns the third and fourth records — no overlap with page 1."""
    failure = await _seed_failure(db_session)
    for _ in range(4):
        await _seed_agent_run(db_session, failure=failure)

    page1 = (
        await client.get("/api/v1/agent-runs", params={"limit": 2, "offset": 0})
    ).json()
    page2 = (
        await client.get("/api/v1/agent-runs", params={"limit": 2, "offset": 2})
    ).json()

    ids_page1 = {item["id"] for item in page1["items"]}
    ids_page2 = {item["id"] for item in page2["items"]}
    assert ids_page1.isdisjoint(ids_page2)


# ===========================================================================
# GET /api/v1/agent-runs/{run_id} — detail
# ===========================================================================


@pytest.mark.asyncio
async def test_get_agent_run_returns_correct_detail(client, db_session):
    """GET /agent-runs/{run_id} returns the full AgentRunItem for that run."""
    failure = await _seed_failure(db_session)
    run = await _seed_agent_run(
        db_session,
        failure=failure,
        agent_name="log_analyzer",
        status=AgentRunStatus.COMPLETED,
        input_summary="stack trace input",
        output_summary="root_cause=NullPointerException",
        duration_ms=780,
        tokens_used=1204,
    )

    response = await client.get(f"/api/v1/agent-runs/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run.id)
    assert body["agent_name"] == "log_analyzer"
    assert body["status"] == str(AgentRunStatus.COMPLETED)
    assert body["test_failure_id"] == str(failure.id)
    assert body["input_summary"] == "stack trace input"
    assert body["output_summary"] == "root_cause=NullPointerException"
    assert body["duration_ms"] == 780
    assert body["tokens_used"] == 1204


@pytest.mark.asyncio
async def test_get_agent_run_optional_fields_can_be_null(client, db_session):
    """Fields like completed_at, input_summary, output_summary may be null."""
    run = await _seed_agent_run(
        db_session,
        input_summary=None,
        output_summary=None,
        completed_at=None,
        duration_ms=None,
        tokens_used=None,
    )

    response = await client.get(f"/api/v1/agent-runs/{run.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["input_summary"] is None
    assert body["output_summary"] is None
    assert body["completed_at"] is None
    assert body["duration_ms"] is None
    assert body["tokens_used"] is None


@pytest.mark.asyncio
async def test_get_agent_run_returns_404_for_unknown_run_id(client, db_session):
    """GET /agent-runs/{unknown_uuid} returns 404 with detail message."""
    unknown_id = uuid.uuid4()

    response = await client.get(f"/api/v1/agent-runs/{unknown_id}")

    assert response.status_code == 404
    assert response.json()["detail"] == "agent run not found"


@pytest.mark.asyncio
async def test_get_agent_run_id_not_visible_in_list_of_other_runs(client, db_session):
    """Fetching a specific run_id only returns that run's data, not others'."""
    failure = await _seed_failure(db_session)
    run_1 = await _seed_agent_run(db_session, failure=failure, agent_name="classifier")
    run_2 = await _seed_agent_run(db_session, failure=failure, agent_name="analyzer")

    response = await client.get(f"/api/v1/agent-runs/{run_1.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(run_1.id)
    assert body["id"] != str(run_2.id)
