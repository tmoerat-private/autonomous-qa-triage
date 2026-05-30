"""Tests for GitHubActionsParser — sync, no async needed."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.integrations.github_actions.parser import GitHubActionsParser
from src.schemas.webhook_payloads import (
    GitHubActionsWebhookPayload,
    GitHubRepository,
    GitHubWorkflowRun,
)

FIXTURES = Path(__file__).parent.parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Sample log strings
# ---------------------------------------------------------------------------

GHA_LOG_WITH_TIMESTAMPS = """\
2024-01-15T10:23:45.1234567Z FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
2024-01-15T10:23:45.2345678Z   File "tests/auth/test_login.py", line 42
2024-01-15T10:23:45.3456789Z E  AssertionError: Expected 200, got 500
2024-01-15T10:23:46.1234567Z PASSED tests/api/test_health.py::test_health_check
"""

GHA_LOG_NO_TIMESTAMPS = """\
FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
PASSED tests/api/test_health.py::test_health_check
"""

GHA_LOG_PASSING_ONLY = """\
2024-01-15T10:23:46.0000000Z PASSED tests/api/test_health.py::test_health_check
2024-01-15T10:23:46.1000000Z PASSED tests/api/test_ready.py::test_readiness_probe
"""

GHA_LOG_MULTIPLE_FAILURES = """\
2024-01-15T10:23:45.0000000Z FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
2024-01-15T10:23:45.1000000Z   File "tests/auth/test_login.py", line 42
2024-01-15T10:23:45.2000000Z E  AssertionError: Expected 200, got 500
2024-01-15T10:23:46.0000000Z FAILED tests/db/test_session.py::TestSession::test_connect - ConnectionError: timeout
2024-01-15T10:23:46.1000000Z   File "tests/db/test_session.py", line 15
2024-01-15T10:23:46.2000000Z E  ConnectionError: timeout
"""

GHA_LOG_DUPLICATE_FAILURE = """\
FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
FAILED tests/auth/test_login.py::TestLogin::test_user_login - AssertionError: Expected 200, got 500
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_gha_payload(
    repo_full_name: str = "org/my-service",
    run_id: int = 9876543210,
    conclusion: str = "failure",
    action: str = "completed",
) -> GitHubActionsWebhookPayload:
    repo = GitHubRepository(
        id=1234,
        full_name=repo_full_name,
        html_url=f"https://github.com/{repo_full_name}",
    )
    run = GitHubWorkflowRun(
        id=run_id,
        name="CI",
        head_branch="main",
        head_sha="abc123def456",
        status="completed",
        conclusion=conclusion,
        html_url=f"https://github.com/{repo_full_name}/actions/runs/{run_id}",
        run_number=42,
        repository=repo,
    )
    return GitHubActionsWebhookPayload(
        action=action,
        workflow_run=run,
        repository=repo,
    )


# ---------------------------------------------------------------------------
# parse_failures tests
# ---------------------------------------------------------------------------


def test_parse_failures_strips_timestamps():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_WITH_TIMESTAMPS)

    assert len(result) == 1
    assert "test_user_login" in result[0].test_name


def test_parse_failures_without_timestamps():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_NO_TIMESTAMPS)

    assert len(result) == 1
    assert "test_user_login" in result[0].test_name


def test_parse_failures_empty():
    parser = GitHubActionsParser()
    result = parser.parse_failures("")
    assert result == []


def test_parse_failures_passing_only():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PASSING_ONLY)
    assert result == []


def test_parse_failures_multiple():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_MULTIPLE_FAILURES)
    assert len(result) == 2


def test_parse_failures_deduplicates():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_DUPLICATE_FAILURE)
    assert len(result) == 1


def test_parse_failures_populates_error_message():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_WITH_TIMESTAMPS)

    assert result[0].error_message is not None
    assert "Expected 200" in result[0].error_message


def test_parse_failures_populates_stack_trace():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_WITH_TIMESTAMPS)

    # Indented trace lines following the FAILED marker should be collected
    assert result[0].stack_trace is not None
    assert "AssertionError" in result[0].stack_trace


def test_parse_failures_populates_test_file():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_WITH_TIMESTAMPS)
    assert result[0].test_file == "tests/auth/test_login.py"


def test_parse_failures_populates_test_suite():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_WITH_TIMESTAMPS)
    assert result[0].test_suite == "TestLogin"


# ---------------------------------------------------------------------------
# extract_run_info tests
# ---------------------------------------------------------------------------


def test_extract_run_info():
    parser = GitHubActionsParser()
    payload = make_gha_payload(repo_full_name="org/my-service", run_id=9876543210)
    repo_full_name, run_id = parser.extract_run_info(payload)
    assert repo_full_name == "org/my-service"
    assert run_id == 9876543210


def test_extract_run_info_from_fixture():
    raw = json.loads((FIXTURES / "github_actions_webhook.json").read_text())
    payload = GitHubActionsWebhookPayload.model_validate(raw)
    parser = GitHubActionsParser()
    repo_full_name, run_id = parser.extract_run_info(payload)
    assert repo_full_name == "org/my-service"
    assert run_id == 9876543210


# ---------------------------------------------------------------------------
# Parametrize: count checks across log variants
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "log_text,expected_count",
    [
        ("", 0),
        (GHA_LOG_PASSING_ONLY, 0),
        (GHA_LOG_NO_TIMESTAMPS, 1),
        (GHA_LOG_WITH_TIMESTAMPS, 1),
        (GHA_LOG_MULTIPLE_FAILURES, 2),
        (GHA_LOG_DUPLICATE_FAILURE, 1),
    ],
)
def test_parse_failures_count_parametrized(log_text: str, expected_count: int):
    parser = GitHubActionsParser()
    result = parser.parse_failures(log_text)
    assert len(result) == expected_count
