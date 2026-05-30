"""Tests for JenkinsParser — sync, no async needed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.integrations.jenkins.parser import JenkinsParser
from src.schemas.webhook_payloads import JenkinsBuild, JenkinsSCM, JenkinsWebhookPayload

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Sample log strings
# ---------------------------------------------------------------------------

SAMPLE_LOG_WITH_FAILURES = """
============================= test session starts ==============================
collected 3 items

FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
  File "tests/auth/test_login.py", line 42, in test_user_login
    assert response.status_code == 200
E  AssertionError: Expected 200, got 500
FAILED tests/db/test_session.py::TestSession::test_connect - ConnectionError: timeout
  File "tests/db/test_session.py", line 15, in test_connect
    conn = await db.connect()
E  ConnectionError: timeout
PASSED tests/api/test_health.py::test_health_check
===== 2 failed, 1 passed in 0.45s =====
"""

SAMPLE_LOG_PASSING_ONLY = """
PASSED tests/api/test_health.py::test_health_check
PASSED tests/api/test_ready.py::test_readiness
===== 2 passed in 0.20s =====
"""

SAMPLE_LOG_DUPLICATE_FAILURE = """
FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
  File "tests/auth/test_login.py", line 42, in test_user_login
E  AssertionError: Expected 200, got 500
FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
  File "tests/auth/test_login.py", line 42, in test_user_login
E  AssertionError: Expected 200, got 500
===== 1 failed in 0.10s =====
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_payload(
    name: str = "my-pipeline",
    build_number: int = 42,
    status: str = "FAILURE",
) -> JenkinsWebhookPayload:
    return JenkinsWebhookPayload(
        name=name,
        url=f"job/{name}/",
        build=JenkinsBuild(
            full_url=f"http://jenkins:8080/job/{name}/{build_number}/",
            number=build_number,
            status=status,
            url=f"job/{name}/{build_number}/",
            scm=JenkinsSCM(
                url="https://github.com/org/my-service",
                branch="main",
                commit="abc123def456",
            ),
        ),
    )


# ---------------------------------------------------------------------------
# parse_failures tests
# ---------------------------------------------------------------------------


def test_parse_failures_returns_correct_count():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    assert len(result) == 2


def test_parse_failures_populates_test_name():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    assert "test_user_login" in result[0].test_name


def test_parse_failures_populates_error_message():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    assert result[0].error_message is not None
    assert "Expected 200" in result[0].error_message


def test_parse_failures_empty_log():
    parser = JenkinsParser()
    result = parser.parse_failures("")
    assert result == []


def test_parse_failures_no_failures():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_PASSING_ONLY)
    assert result == []


def test_parse_failures_from_fixture():
    fixture_log = (FIXTURES / "sample_build_log.txt").read_text()
    parser = JenkinsParser()
    result = parser.parse_failures(fixture_log)

    assert len(result) >= 1
    for failure in result:
        assert failure.test_name, "Every parsed failure must have a non-empty test_name"


def test_parse_failures_deduplicates():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_DUPLICATE_FAILURE)
    assert len(result) == 1


def test_parse_failures_populates_stack_trace():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    # The first failure has indented trace lines following it
    assert result[0].stack_trace is not None
    assert "AssertionError" in result[0].stack_trace


def test_parse_failures_populates_test_suite():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    # "tests/auth/test_login.py::TestLogin::test_user_login" → suite is "TestLogin"
    assert result[0].test_suite == "TestLogin"


def test_parse_failures_populates_test_file():
    parser = JenkinsParser()
    result = parser.parse_failures(SAMPLE_LOG_WITH_FAILURES)
    assert result[0].test_file == "tests/auth/test_login.py"


# ---------------------------------------------------------------------------
# extract_job_info tests
# ---------------------------------------------------------------------------


def test_extract_job_info():
    parser = JenkinsParser()
    payload = make_payload(name="my-pipeline", build_number=42)
    job_name, build_number = parser.extract_job_info(payload)
    assert job_name == "my-pipeline"
    assert build_number == 42


def test_extract_job_info_from_fixture():
    raw = json.loads((FIXTURES / "jenkins_webhook.json").read_text())
    payload = JenkinsWebhookPayload.model_validate(raw)
    parser = JenkinsParser()
    job_name, build_number = parser.extract_job_info(payload)
    assert job_name == "my-pipeline"
    assert build_number == 42


# ---------------------------------------------------------------------------
# Parametrize: various log formats
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "log_text,expected_count",
    [
        ("", 0),
        (SAMPLE_LOG_PASSING_ONLY, 0),
        (SAMPLE_LOG_WITH_FAILURES, 2),
        (SAMPLE_LOG_DUPLICATE_FAILURE, 1),
    ],
)
def test_parse_failures_count_parametrized(log_text: str, expected_count: int):
    parser = JenkinsParser()
    result = parser.parse_failures(log_text)
    assert len(result) == expected_count
