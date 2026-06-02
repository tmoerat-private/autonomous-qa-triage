"""Unit tests for src/services/webhook_service.py.

Covers:
  1. test_valid_github_signature_accepted        — real HMAC-SHA256 accepted
  2. test_invalid_github_signature_rejected      — wrong digest raises 401
  3. test_missing_signature_rejected             — absent header raises 401
  4. test_jenkins_signature_accepted             — Jenkins sha256= format accepted
  5. test_celery_task_dispatched_on_valid_webhook — run_triage_pipeline.delay called
  6. test_unknown_provider_rejected              — unsupported provider raises 400
  7. test_payload_stored_as_pipeline_event       — PipelineEvent row written to DB

All tests patch `get_settings` so no .env file or real secret is required.
The Celery task is patched with MagicMock so no broker is needed.
Tests 1-6 also patch PipelineEventRepository.create to avoid a DB round-trip
where the assertion is about service behavior, not persistence.
Test 7 uses the real `db_session` fixture to verify persistence end-to-end.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.config.constants import CIProvider
from src.models.pipeline_event import PipelineEvent
from src.services.webhook_service import WebhookService

# ---------------------------------------------------------------------------
# Test constants
# ---------------------------------------------------------------------------

GITHUB_SECRET = "github-test-secret-abc123"
JENKINS_SECRET = "jenkins-test-secret-xyz789"

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"

# Load fixture payloads once at module level.
GITHUB_PAYLOAD: dict = json.loads(
    (FIXTURES / "github_actions_webhook.json").read_text()
)
JENKINS_PAYLOAD: dict = json.loads(
    (FIXTURES / "jenkins_webhook.json").read_text()
)

GITHUB_BODY: bytes = json.dumps(GITHUB_PAYLOAD).encode()
JENKINS_BODY: bytes = json.dumps(JENKINS_PAYLOAD).encode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _github_sig(body: bytes, secret: str = GITHUB_SECRET) -> str:
    """Return a valid GitHub-style 'sha256=<hex>' signature."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _jenkins_sig(body: bytes, secret: str = JENKINS_SECRET) -> str:
    """Return a valid Jenkins-style 'sha256=<hex>' signature."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _make_mock_settings(
    github_secret: str = GITHUB_SECRET,
    jenkins_secret: str = JENKINS_SECRET,
) -> MagicMock:
    """Return a mock Settings object with predictable webhook secrets."""
    s = MagicMock()
    s.github_webhook_secret = github_secret
    s.jenkins_webhook_secret = jenkins_secret
    return s


def _make_mock_pipeline_event(provider: str = "github_actions") -> MagicMock:
    """Return a fake PipelineEvent with a stable id."""
    event = MagicMock(spec=PipelineEvent)
    event.id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    event.provider = provider
    return event


# ---------------------------------------------------------------------------
# Context managers shared across tests
# ---------------------------------------------------------------------------

# We patch three things in every test that does not exercise the DB:
#   1. get_settings  — returns our test secrets
#   2. PipelineEventRepository.create — returns a fake event, no real DB write
#   3. run_triage_pipeline — the Celery task, no broker needed


def _patch_settings(mock_settings: MagicMock | None = None):
    if mock_settings is None:
        mock_settings = _make_mock_settings()
    return patch("src.services.webhook_service.get_settings", return_value=mock_settings)


def _patch_celery():
    return patch("src.services.webhook_service.run_triage_pipeline")


# ===========================================================================
# Test 1 — valid GitHub signature is accepted
# ===========================================================================


async def test_valid_github_signature_accepted():
    """process_webhook returns 'accepted' when a correct GitHub HMAC-SHA256 sig is provided."""
    fake_event = _make_mock_pipeline_event("github_actions")
    mock_create = AsyncMock(return_value=fake_event)

    with (
        _patch_settings(),
        patch(
            "src.services.webhook_service.PipelineEventRepository.create",
            mock_create,
        ),
        _patch_celery(),
    ):
        service = WebhookService(db_session=MagicMock())
        result = await service.process_webhook(
            provider=CIProvider.GITHUB_ACTIONS,
            raw_body=GITHUB_BODY,
            signature_header=_github_sig(GITHUB_BODY),
            payload_dict=GITHUB_PAYLOAD,
        )

    assert result["status"] == "accepted"
    assert "pipeline_event_id" in result


# ===========================================================================
# Test 2 — invalid GitHub signature raises 401
# ===========================================================================


async def test_invalid_github_signature_rejected():
    """process_webhook raises HTTPException(401) when the signature digest is wrong."""
    with _patch_settings():
        service = WebhookService(db_session=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await service.process_webhook(
                provider=CIProvider.GITHUB_ACTIONS,
                raw_body=GITHUB_BODY,
                signature_header="sha256=0000000000000000000000000000000000000000000000000000000000000000",
                payload_dict=GITHUB_PAYLOAD,
            )

    assert exc_info.value.status_code == 401
    assert "signature" in exc_info.value.detail.lower()


# ===========================================================================
# Test 3 — missing signature header raises 401
# ===========================================================================


async def test_missing_signature_rejected():
    """process_webhook raises HTTPException(401) when no signature header is sent."""
    with _patch_settings():
        service = WebhookService(db_session=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await service.process_webhook(
                provider=CIProvider.GITHUB_ACTIONS,
                raw_body=GITHUB_BODY,
                signature_header=None,
                payload_dict=GITHUB_PAYLOAD,
            )

    assert exc_info.value.status_code == 401


# ===========================================================================
# Test 4 — valid Jenkins signature is accepted
# ===========================================================================


async def test_jenkins_signature_accepted():
    """process_webhook returns 'accepted' when a correct Jenkins sha256= sig is provided."""
    fake_event = _make_mock_pipeline_event("jenkins")
    mock_create = AsyncMock(return_value=fake_event)

    with (
        _patch_settings(),
        patch(
            "src.services.webhook_service.PipelineEventRepository.create",
            mock_create,
        ),
        _patch_celery(),
    ):
        service = WebhookService(db_session=MagicMock())
        result = await service.process_webhook(
            provider=CIProvider.JENKINS,
            raw_body=JENKINS_BODY,
            signature_header=_jenkins_sig(JENKINS_BODY),
            payload_dict=JENKINS_PAYLOAD,
        )

    assert result["status"] == "accepted"


# ===========================================================================
# Test 5 — Celery task is dispatched after a valid webhook
# ===========================================================================


async def test_celery_task_dispatched_on_valid_webhook():
    """run_triage_pipeline.delay() is called exactly once with the new pipeline_event_id."""
    fake_event = _make_mock_pipeline_event("github_actions")
    fake_event.id = "11111111-2222-3333-4444-555555555555"
    mock_create = AsyncMock(return_value=fake_event)

    mock_task = MagicMock()

    with (
        _patch_settings(),
        patch(
            "src.services.webhook_service.PipelineEventRepository.create",
            mock_create,
        ),
        patch("src.services.webhook_service.run_triage_pipeline", mock_task),
    ):
        service = WebhookService(db_session=MagicMock())
        await service.process_webhook(
            provider=CIProvider.GITHUB_ACTIONS,
            raw_body=GITHUB_BODY,
            signature_header=_github_sig(GITHUB_BODY),
            payload_dict=GITHUB_PAYLOAD,
        )

    mock_task.delay.assert_called_once_with(str(fake_event.id))


# ===========================================================================
# Test 6 — unknown provider raises 400
# ===========================================================================


async def test_unknown_provider_rejected():
    """process_webhook raises HTTPException(400) for a provider not in the registry."""
    with _patch_settings():
        service = WebhookService(db_session=MagicMock())
        with pytest.raises(HTTPException) as exc_info:
            await service.process_webhook(
                provider="unknown_ci_system",
                raw_body=b'{"key": "value"}',
                signature_header=None,
                payload_dict={"key": "value"},
            )

    assert exc_info.value.status_code == 400
    assert "unsupported" in exc_info.value.detail.lower()


# ===========================================================================
# Test 7 — PipelineEvent is persisted to the real test database
# ===========================================================================


async def test_payload_stored_as_pipeline_event(db_session):
    """A valid webhook call writes exactly one PipelineEvent row to the database."""
    mock_task = MagicMock()

    with (
        _patch_settings(
            _make_mock_settings(
                github_secret=GITHUB_SECRET,
                jenkins_secret=JENKINS_SECRET,
            )
        ),
        patch("src.services.webhook_service.run_triage_pipeline", mock_task),
    ):
        service = WebhookService(db_session=db_session)
        result = await service.process_webhook(
            provider=CIProvider.GITHUB_ACTIONS,
            raw_body=GITHUB_BODY,
            signature_header=_github_sig(GITHUB_BODY),
            payload_dict=GITHUB_PAYLOAD,
        )

    # Verify the returned pipeline_event_id refers to a real row.
    event_id = result["pipeline_event_id"]
    stmt = select(PipelineEvent).where(PipelineEvent.id == event_id)
    db_result = await db_session.execute(stmt)
    event = db_result.scalar_one_or_none()

    assert event is not None, "Expected a PipelineEvent row to be written to the DB"
    assert event.provider == CIProvider.GITHUB_ACTIONS
    assert event.repository == "org/my-service"
    assert event.branch == "main"
    assert event.commit_sha == "abc123def456"
    assert event.status == "failure"
    assert event.raw_payload == GITHUB_PAYLOAD
