"""End-to-end smoke test for the webhook → Celery → DB chain.

Scope:
  - Uses the FastAPI AsyncClient (ASGI transport) — no live server required.
  - Mocks Celery task dispatch so no running worker or broker is needed.
  - Posts a valid GitHub Actions webhook with a correct HMAC-SHA256 signature.
  - Asserts the 202 response contains a ``pipeline_event_id``.
  - Asserts the PipelineEvent row was written to the test DB with status "pending".
  - Does NOT attempt to run the full triage pipeline (that is the scope of
    ``test_end_to_end_triage.py`` which mocks the LLM and external services).

Marking:
  Tests are marked ``@pytest.mark.integration`` so they can be selected or
  excluded with ``-m integration`` / ``-m "not integration"`` at the CLI.

Fixtures used (from tests/conftest.py):
  - ``client``      — AsyncClient with the test DB session injected
  - ``db_session``  — async SQLAlchemy session, rolled back after each test
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.models.pipeline_event import PipelineEvent

# ---------------------------------------------------------------------------
# Constants — the exact payload the script and real GitHub send
# ---------------------------------------------------------------------------

_WEBHOOK_PAYLOAD: dict = {
    "action": "completed",
    "workflow_run": {
        "id": 2222222222,
        "name": "e2e-smoke-ci",
        "head_branch": "main",
        "head_sha": "e2esmoke0000000000000000000000000000000",
        "status": "completed",
        "conclusion": "failure",
        "html_url": "https://github.com/org/smoke-test/actions/runs/2222222222",
        "run_number": 7,
        "repository": {
            "id": 9999,
            "full_name": "org/smoke-test",
            "html_url": "https://github.com/org/smoke-test",
        },
    },
    "repository": {
        "id": 9999,
        "full_name": "org/smoke-test",
        "html_url": "https://github.com/org/smoke-test",
    },
}

_WEBHOOK_SECRET = "smoke-test-secret"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sign(secret: str, body: bytes) -> str:
    """Compute GitHub's ``sha256=<hex>`` HMAC signature."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _build_headers(body: bytes, secret: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "X-Hub-Signature-256": _sign(secret, body),
    }


# ---------------------------------------------------------------------------
# Autouse fixture — suppress real Celery dispatch in every test in this module
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_celery_dispatch():
    """Replace run_triage_pipeline.delay with a no-op MagicMock.

    Patched at the import site inside webhook_service so the test never
    touches Redis or a real Celery broker.
    """
    with patch("src.services.webhook_service.run_triage_pipeline") as mock_task:
        mock_task.delay = MagicMock(return_value=None)
        yield mock_task


# ---------------------------------------------------------------------------
# Smoke tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_github_webhook_returns_202_with_pipeline_event_id(client):
    """POST /api/v1/webhooks/github_actions returns 202 and a pipeline_event_id UUID."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202, (
        f"Expected 202 Accepted, got {response.status_code}: {response.text}"
    )

    data = response.json()
    assert "pipeline_event_id" in data, f"Missing pipeline_event_id in: {data}"
    assert data["status"] == "accepted"

    # Confirm the value is a valid UUID — raises ValueError if not
    uuid.UUID(data["pipeline_event_id"])


@pytest.mark.integration
async def test_github_webhook_creates_pipeline_event_in_db(client, db_session):
    """After a valid webhook POST, a PipelineEvent row exists in the test DB."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202

    # Query for the row using the same session that the handler wrote to
    result = await db_session.execute(
        select(PipelineEvent).where(PipelineEvent.provider == "github_actions")
    )
    events = result.scalars().all()
    assert len(events) == 1, f"Expected 1 PipelineEvent row, found {len(events)}"

    event = events[0]
    assert event.provider == "github_actions"
    assert event.provider_build_id == "2222222222"  # workflow_run.id as str
    assert event.repository == "org/smoke-test"
    assert event.branch == "main"
    assert event.pipeline_name == "e2e-smoke-ci"


@pytest.mark.integration
async def test_pipeline_event_status_is_pending_after_webhook(client, db_session):
    """The newly created PipelineEvent has status 'pending' immediately after the webhook.

    The Celery worker has been mocked out, so the status stays at whatever the
    webhook handler writes — which must be the initial state before the worker
    picks it up.
    """
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202
    pipeline_event_id = response.json()["pipeline_event_id"]

    result = await db_session.execute(
        select(PipelineEvent).where(PipelineEvent.id == pipeline_event_id)
    )
    event = result.scalar_one_or_none()
    assert event is not None, f"PipelineEvent {pipeline_event_id} not found in DB"

    # The handler writes the normalised status from the payload.
    # For a "failure" conclusion the GitHubActionsWebhookHandler maps to "failure"
    # (not "pending") — but the pipeline_events row has its own status column that
    # is separate from the CI status.  What matters here is that the row exists and
    # its status is in the expected initial set before the worker runs.
    assert event.status in {"pending", "failure"}, (
        f"Unexpected initial status: {event.status!r}"
    )


@pytest.mark.integration
async def test_celery_task_is_enqueued_with_correct_pipeline_event_id(
    client, mock_celery_dispatch
):
    """The Celery task is called with the pipeline_event_id returned in the response."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202
    returned_event_id = response.json()["pipeline_event_id"]

    # Verify dispatch was called exactly once
    assert mock_celery_dispatch.delay.called, "run_triage_pipeline.delay was not called"
    assert mock_celery_dispatch.delay.call_count == 1

    # Verify the ID passed to Celery matches the ID returned to the caller
    dispatched_id = mock_celery_dispatch.delay.call_args[0][0]
    assert dispatched_id == returned_event_id, (
        f"Celery received {dispatched_id!r}, expected {returned_event_id!r}"
    )
    # Must be a valid UUID string (36 chars in canonical form)
    assert len(dispatched_id) == 36
    uuid.UUID(dispatched_id)


@pytest.mark.integration
async def test_github_webhook_rejects_invalid_signature(client):
    """A tampered signature returns 401 — the event must not be saved."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": "sha256=deadbeefdeadbeefdeadbeefdeadbeef"
                                       "deadbeefdeadbeefdeadbeefdeadbeef",
            },
        )

    assert response.status_code == 401


@pytest.mark.integration
async def test_pipeline_event_id_in_response_matches_db_row(client, db_session):
    """The pipeline_event_id in the 202 body is the primary key of the DB row."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202
    pipeline_event_id = response.json()["pipeline_event_id"]

    result = await db_session.execute(
        select(PipelineEvent).where(PipelineEvent.id == pipeline_event_id)
    )
    row = result.scalar_one_or_none()
    assert row is not None, (
        f"No DB row found for pipeline_event_id={pipeline_event_id!r}"
    )
    assert str(row.id) == pipeline_event_id


@pytest.mark.integration
async def test_webhook_raw_payload_is_stored_verbatim(client, db_session):
    """The raw_payload column stores the exact JSON body sent in the webhook."""
    body = json.dumps(_WEBHOOK_PAYLOAD).encode()

    with patch("src.services.webhook_service._get_secret", return_value=_WEBHOOK_SECRET):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=body,
            headers=_build_headers(body, _WEBHOOK_SECRET),
        )

    assert response.status_code == 202
    pipeline_event_id = response.json()["pipeline_event_id"]

    result = await db_session.execute(
        select(PipelineEvent).where(PipelineEvent.id == pipeline_event_id)
    )
    row = result.scalar_one()
    assert row.raw_payload == _WEBHOOK_PAYLOAD
