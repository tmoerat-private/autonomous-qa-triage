"""Tests for JenkinsWebhookHandler — sync, no async needed."""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from src.config.constants import CIProvider, PipelineStatus
from src.integrations.jenkins.webhook_handler import JenkinsWebhookHandler

# ---------------------------------------------------------------------------
# Sample payload
# ---------------------------------------------------------------------------

JENKINS_PAYLOAD: dict = {
    "name": "my-pipeline",
    "url": "job/my-pipeline/",
    "build": {
        "full_url": "http://jenkins:8080/job/my-pipeline/42/",
        "number": 42,
        "status": "FAILURE",
        "url": "job/my-pipeline/42/",
        "scm": {
            "url": "https://github.com/org/my-service",
            "branch": "main",
            "commit": "abc123",
        },
        "artifacts": {},
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_signature(secret: str, payload: dict) -> str:
    body = json.dumps(payload, separators=(",", ":")).encode()
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def sign_bytes(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ---------------------------------------------------------------------------
# parse() tests
# ---------------------------------------------------------------------------


def test_parse_valid_payload():
    handler = JenkinsWebhookHandler()
    event = handler.parse(JENKINS_PAYLOAD)

    assert event.provider == CIProvider.JENKINS
    assert event.provider_build_id == "42"
    assert event.pipeline_name == "my-pipeline"
    assert event.branch == "main"


def test_parse_valid_payload_repository():
    handler = JenkinsWebhookHandler()
    event = handler.parse(JENKINS_PAYLOAD)
    assert event.repository == "https://github.com/org/my-service"


def test_parse_valid_payload_commit_sha():
    handler = JenkinsWebhookHandler()
    event = handler.parse(JENKINS_PAYLOAD)
    assert event.commit_sha == "abc123"


def test_parse_invalid_payload_raises():
    handler = JenkinsWebhookHandler()
    with pytest.raises(ValueError, match="Invalid Jenkins payload"):
        handler.parse({})


def test_parse_missing_build_raises():
    handler = JenkinsWebhookHandler()
    with pytest.raises(ValueError):
        handler.parse({"name": "pipeline-only-no-build", "url": "job/foo/"})


def test_parse_preserves_raw_payload():
    handler = JenkinsWebhookHandler()
    event = handler.parse(JENKINS_PAYLOAD)
    assert event.raw_payload == JENKINS_PAYLOAD


# ---------------------------------------------------------------------------
# Status mapping tests (parametrized)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "build_status,expected",
    [
        ("FAILURE", PipelineStatus.FAILURE),
        ("UNSTABLE", PipelineStatus.FAILURE),
        ("ABORTED", PipelineStatus.FAILURE),
        ("SUCCESS", PipelineStatus.SUCCESS),
        ("UNKNOWN", PipelineStatus.ERROR),
    ],
)
def test_parse_status_mapping(build_status: str, expected: PipelineStatus):
    handler = JenkinsWebhookHandler()
    payload = {
        **JENKINS_PAYLOAD,
        "build": {**JENKINS_PAYLOAD["build"], "status": build_status},
    }
    event = handler.parse(payload)
    assert event.status == expected


# ---------------------------------------------------------------------------
# verify_signature() tests
# ---------------------------------------------------------------------------


def test_verify_signature_valid():
    handler = JenkinsWebhookHandler()
    secret = "my-webhook-secret"
    body = b'{"name": "my-pipeline"}'
    sig = sign_bytes(secret, body)

    assert handler.verify_signature(secret, body, sig) is True


def test_verify_signature_invalid():
    handler = JenkinsWebhookHandler()
    body = b'{"name": "my-pipeline"}'

    assert handler.verify_signature("correct-secret", body, "sha256=deadbeef") is False


def test_verify_signature_missing():
    handler = JenkinsWebhookHandler()
    body = b'{"name": "my-pipeline"}'

    assert handler.verify_signature("my-webhook-secret", body, "") is False


def test_verify_signature_wrong_secret():
    handler = JenkinsWebhookHandler()
    body = b'{"name": "my-pipeline"}'
    sig = sign_bytes("correct-secret", body)

    assert handler.verify_signature("wrong-secret", body, sig) is False


def test_verify_signature_raw_hex_without_prefix():
    """Signatures without the sha256= prefix are also accepted."""
    handler = JenkinsWebhookHandler()
    secret = "my-webhook-secret"
    body = b'{"name": "my-pipeline"}'
    raw_hex = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    assert handler.verify_signature(secret, body, raw_hex) is True


# ---------------------------------------------------------------------------
# Parametrize: invalid/missing signatures
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "sig",
    [
        "",
        "sha256=",
        "sha256=notahexvalue!!",
        "sha256=0000000000000000000000000000000000000000000000000000000000000000",
    ],
)
def test_verify_signature_rejects_bad_values(sig: str):
    handler = JenkinsWebhookHandler()
    body = b'{"name": "my-pipeline"}'
    assert handler.verify_signature("real-secret", body, sig) is False
