"""Integration tests for the webhook endpoint POST /api/v1/webhooks/{provider}.

All tests are async (asyncio_mode = "auto" in pyproject.toml).
External calls (Celery task dispatch) are mocked at the module level via
autouse fixtures so no real Redis/Celery connection is needed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pathlib
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from src.models.pipeline_event import PipelineEvent

FIXTURES = pathlib.Path(__file__).parent.parent / "fixtures"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def sign_payload(secret: str, payload_bytes: bytes, prefix: str = "sha256=") -> str:
    digest = hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()
    return f"{prefix}{digest}"


# ---------------------------------------------------------------------------
# Module-level autouse fixture — suppress real Celery dispatch in every test
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def mock_celery():
    """Replace the Celery task with a no-op MagicMock for every test."""
    with patch("src.services.webhook_service.run_triage_pipeline") as mock_task:
        mock_task.delay = MagicMock(return_value=None)
        yield mock_task


# ---------------------------------------------------------------------------
# Test 1 — Jenkins webhook accepted (valid signature)
# ---------------------------------------------------------------------------


async def test_jenkins_webhook_accepted(client):
    payload_dict = load_fixture("jenkins_webhook.json")
    payload_bytes = json.dumps(payload_dict).encode()
    secret = "test-secret"
    sig = sign_payload(secret, payload_bytes)

    with patch("src.services.webhook_service._get_secret", return_value=secret):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Jenkins-Signature": sig,
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert "pipeline_event_id" in body


# ---------------------------------------------------------------------------
# Test 2 — GitHub Actions webhook accepted (valid signature)
# ---------------------------------------------------------------------------


async def test_github_actions_webhook_accepted(client):
    payload_dict = load_fixture("github_actions_webhook.json")
    # Fixture has action="completed" which the GHA handler requires
    payload_bytes = json.dumps(payload_dict).encode()
    secret = "test-secret"
    sig = sign_payload(secret, payload_bytes)

    with patch("src.services.webhook_service._get_secret", return_value=secret):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": sig,
            },
        )

    assert response.status_code == 202
    body = response.json()
    assert "pipeline_event_id" in body


# ---------------------------------------------------------------------------
# Test 3 — Invalid signature returns 401
# ---------------------------------------------------------------------------


async def test_webhook_invalid_signature_returns_401(client):
    body = b'{"bad": "sig"}'

    # Provide a non-empty secret so the service enforces verification
    with patch("src.services.webhook_service._get_secret", return_value="real-secret"):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Jenkins-Signature": "sha256=deadbeef",
            },
        )

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Test 4 — Unsupported provider returns 400
# ---------------------------------------------------------------------------


async def test_webhook_unsupported_provider_returns_400(client):
    payload_bytes = json.dumps({"event": "push"}).encode()

    response = await client.post(
        "/api/v1/webhooks/gitlab",
        content=payload_bytes,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Test 5 — Celery task is enqueued after a valid webhook
# ---------------------------------------------------------------------------


async def test_celery_task_enqueued_on_valid_webhook(client, mock_celery):
    payload_dict = load_fixture("jenkins_webhook.json")
    payload_bytes = json.dumps(payload_dict).encode()
    secret = "test-secret"
    sig = sign_payload(secret, payload_bytes)

    with patch("src.services.webhook_service._get_secret", return_value=secret):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Jenkins-Signature": sig,
            },
        )

    assert response.status_code == 202
    assert mock_celery.delay.called

    # The task receives a pipeline_event_id UUID string
    call_args = mock_celery.delay.call_args
    pipeline_event_id = call_args[0][0]
    assert isinstance(pipeline_event_id, str)
    assert len(pipeline_event_id) == 36  # UUID4 canonical form length


# ---------------------------------------------------------------------------
# Test 6 — Pipeline event is persisted to the DB after a valid webhook
# ---------------------------------------------------------------------------


async def test_pipeline_event_saved_to_db(client, db_session):
    """After a valid Jenkins webhook, a PipelineEvent row exists in the DB."""
    payload_dict = load_fixture("jenkins_webhook.json")
    payload_bytes = json.dumps(payload_dict).encode()
    secret = "test-secret"
    sig = sign_payload(secret, payload_bytes)

    with patch("src.services.webhook_service._get_secret", return_value=secret):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Jenkins-Signature": sig,
            },
        )

    assert response.status_code == 202

    # Query through the same db_session (shared via conftest fixture)
    result = await db_session.execute(
        select(PipelineEvent).where(PipelineEvent.provider == "jenkins")
    )
    events = result.scalars().all()
    assert len(events) == 1
    assert events[0].provider_build_id == "42"


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


async def test_jenkins_webhook_returns_pipeline_event_id_as_uuid(client):
    """The pipeline_event_id in the response body is a valid UUID string."""
    import uuid

    payload_dict = load_fixture("jenkins_webhook.json")
    payload_bytes = json.dumps(payload_dict).encode()
    secret = "test-secret"
    sig = sign_payload(secret, payload_bytes)

    with patch("src.services.webhook_service._get_secret", return_value=secret):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                "X-Jenkins-Signature": sig,
            },
        )

    assert response.status_code == 202
    body = response.json()
    # Should not raise — the value must be a valid UUID4
    uuid.UUID(body["pipeline_event_id"])


async def test_webhook_no_secret_configured_skips_verification(client):
    """When no secret is configured the service skips verification and accepts the payload."""
    payload_dict = load_fixture("jenkins_webhook.json")
    payload_bytes = json.dumps(payload_dict).encode()

    # Empty string signals "no secret configured" to the service
    with patch("src.services.webhook_service._get_secret", return_value=""):
        response = await client.post(
            "/api/v1/webhooks/jenkins",
            content=payload_bytes,
            headers={
                "Content-Type": "application/json",
                # No signature header at all
            },
        )

    assert response.status_code == 202


async def test_github_actions_non_completed_action_returns_422(client):
    """GitHub Actions handler rejects non-completed actions with 422."""
    payload_dict = {**load_fixture("github_actions_webhook.json"), "action": "requested"}
    payload_bytes = json.dumps(payload_dict).encode()

    with patch("src.services.webhook_service._get_secret", return_value=""):
        response = await client.post(
            "/api/v1/webhooks/github_actions",
            content=payload_bytes,
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422
