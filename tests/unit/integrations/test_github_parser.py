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
# Playwright list-reporter sample logs
# ---------------------------------------------------------------------------

GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE = """\
Running 75 tests using 5 workers

  1) [chromium] › tests/navigation/navigation.spec.ts:39:7 › Sidebar navigation › dark mode toggle

    Error: expect(locator).toBeVisible() failed

    Locator: locator('[data-testid="theme-toggle"]')
    Expected: visible
    Received: hidden

      37 |   test('dark mode toggle', async ({ page }) => {
      38 |     await page.goto('/settings');
    > 39 |     await expect(page.getByTestId('theme-toggle')).toBeVisible();
         |                                                     ^

    at /home/runner/work/triage-qa-automation/triage-qa-automation/tests/navigation/navigation.spec.ts:39:7

  1 failed
    [chromium] › tests/navigation/navigation.spec.ts:39:7 › Sidebar navigation › dark mode toggle
  74 passed (3.3m)
"""  # noqa: RUF001

GHA_LOG_PLAYWRIGHT_NO_DESCRIBE_BLOCK = """\
  1) [webkit] › tests/smoke/health.spec.ts:10:5 › health check returns 200

    Error: expect(received).toBe(expected)

  1 failed
  10 passed (45s)
"""  # noqa: RUF001

GHA_LOG_PLAYWRIGHT_MULTIPLE_FAILURES = """\
  1) [chromium] › tests/auth/login.spec.ts:20:3 › Login flow › shows error on bad password

    Error: expect(locator).toContainText(expected) failed

  2) [firefox] › tests/checkout/cart.spec.ts:55:9 › Checkout › applies discount code

    Error: expect(received).toBe(expected)

  2 failed
  68 passed (4.1m)
"""  # noqa: RUF001

GHA_LOG_PLAYWRIGHT_WITH_ANSI = (
    "\x1b[2m  1) \x1b[22m\x1b[31m[chromium]\x1b[39m \x1b[2m›\x1b[22m "  # noqa: RUF001
    "tests/navigation/navigation.spec.ts:39:7 \x1b[2m›\x1b[22m "  # noqa: RUF001
    "Sidebar navigation \x1b[2m›\x1b[22m dark mode toggle\x1b[39m\x1b[22m\n"  # noqa: RUF001
    "\n"
    "    \x1b[31mError: expect(locator).toBeVisible() failed\x1b[39m\n"
    "\n"
    "  1 failed\n"
)

GHA_LOG_PLAYWRIGHT_DUPLICATE = """\
  1) [chromium] › tests/auth/login.spec.ts:20:3 › Login flow › shows error on bad password

    Error: expect(locator).toContainText(expected) failed

  2) [chromium] › tests/auth/login.spec.ts:20:3 › Login flow › shows error on bad password

    Error: expect(locator).toContainText(expected) failed

  1 failed
"""  # noqa: RUF001


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
# Playwright list-reporter format tests
# ---------------------------------------------------------------------------


def test_parse_playwright_single_failure_count():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    assert len(result) == 1


def test_parse_playwright_test_name_and_file():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    failure = result[0]
    assert failure.test_name == "dark mode toggle"
    assert failure.test_file == "tests/navigation/navigation.spec.ts"


def test_parse_playwright_test_suite_combines_project_and_describe_blocks():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    assert result[0].test_suite == "chromium › Sidebar navigation"  # noqa: RUF001


def test_parse_playwright_error_message_extracted_from_error_line():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    assert result[0].error_message == "expect(locator).toBeVisible() failed"


def test_parse_playwright_stack_trace_captures_detail_block():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    stack_trace = result[0].stack_trace
    assert stack_trace is not None
    assert "Locator:" in stack_trace
    assert "theme-toggle" in stack_trace


def test_parse_playwright_stack_trace_excludes_run_summary_lines():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE)
    stack_trace = result[0].stack_trace
    assert stack_trace is not None
    assert "1 failed" not in stack_trace
    assert "74 passed" not in stack_trace


def test_parse_playwright_no_describe_block_uses_project_only_suite():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_NO_DESCRIBE_BLOCK)

    assert len(result) == 1
    failure = result[0]
    assert failure.test_suite == "webkit"
    assert failure.test_name == "health check returns 200"
    assert failure.test_file == "tests/smoke/health.spec.ts"


def test_parse_playwright_multiple_failures():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_MULTIPLE_FAILURES)

    assert len(result) == 2
    names = {f.test_name for f in result}
    assert "shows error on bad password" in names
    assert "applies discount code" in names


def test_parse_playwright_strips_ansi_codes():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_WITH_ANSI)

    assert len(result) == 1
    failure = result[0]
    assert failure.test_name == "dark mode toggle"
    assert failure.test_suite == "chromium › Sidebar navigation"  # noqa: RUF001
    assert failure.error_message == "expect(locator).toBeVisible() failed"

    # No leftover escape sequences in any parsed field.
    for value in (
        failure.test_name,
        failure.test_suite,
        failure.test_file,
        failure.error_message,
    ):
        assert "\x1b" not in (value or "")


def test_parse_playwright_deduplicates_by_test_name():
    parser = GitHubActionsParser()
    result = parser.parse_failures(GHA_LOG_PLAYWRIGHT_DUPLICATE)
    assert len(result) == 1


def test_parse_playwright_fallback_only_when_pytest_finds_nothing():
    """When a log contains pytest FAILED lines, the pytest parser wins and
    Playwright-style content elsewhere in the log is ignored."""
    mixed_log = GHA_LOG_WITH_TIMESTAMPS + "\n" + GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE
    parser = GitHubActionsParser()
    result = parser.parse_failures(mixed_log)

    assert len(result) == 1
    assert "test_user_login" in result[0].test_name


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
        (GHA_LOG_PLAYWRIGHT_SINGLE_FAILURE, 1),
        (GHA_LOG_PLAYWRIGHT_NO_DESCRIBE_BLOCK, 1),
        (GHA_LOG_PLAYWRIGHT_MULTIPLE_FAILURES, 2),
        (GHA_LOG_PLAYWRIGHT_DUPLICATE, 1),
        (GHA_LOG_PLAYWRIGHT_WITH_ANSI, 1),
    ],
)
def test_parse_failures_count_parametrized(log_text: str, expected_count: int):
    parser = GitHubActionsParser()
    result = parser.parse_failures(log_text)
    assert len(result) == expected_count
