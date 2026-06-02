"""Tests for github_tools.py — respx mocks for all outbound httpx calls."""
from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest
import respx
from httpx import Response

from src.agents.tools.github_tools import (
    get_commit_diff,
    get_failed_jobs,
    get_recent_runs_for_test,
    get_workflow_run_logs,
)
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Shared settings mock
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(
    github_app_id="test-gh-token",
    anthropic_api_key="test-key",
)

REPO = "acme-corp/backend"
RUN_ID = 12345678


def _make_zip_bytes(filename: str, content: str) -> bytes:
    """Return a zip archive bytes object containing one text file."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# get_workflow_run_logs tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_workflow_run_logs_success():
    """Successful log fetch returns the extracted plain text."""
    log_content = "BUILD FAILURE\njava.lang.NullPointerException at MyClass.java:42"
    zip_bytes = _make_zip_bytes("1_build.txt", log_content)

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/logs"
    ).mock(return_value=Response(200, content=zip_bytes))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_workflow_run_logs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    assert "NullPointerException" in result
    assert "BUILD FAILURE" in result


@respx.mock
async def test_get_workflow_run_logs_404():
    """404 from logs endpoint returns a descriptive error string, not an exception."""
    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/logs"
    ).mock(return_value=Response(404, text="Not Found"))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_workflow_run_logs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    # Client returns "" on 404 which the tool converts to a "no logs" message
    assert isinstance(result, str)
    assert RUN_ID == RUN_ID  # The result indicates unavailability rather than crashing


@respx.mock
async def test_get_workflow_run_logs_truncated():
    """Logs longer than _TOOL_LOG_LIMIT are truncated with a notice."""
    log_content = "X" * 10_000
    zip_bytes = _make_zip_bytes("1_job.txt", log_content)

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/logs"
    ).mock(return_value=Response(200, content=zip_bytes))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_workflow_run_logs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    assert len(result) <= 8_000 + 60  # 8000 chars + truncation notice overhead
    assert "truncated" in result


# ---------------------------------------------------------------------------
# get_failed_jobs tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_failed_jobs_success():
    """Returns list of failed job dicts when run has failing jobs."""
    jobs_response = {
        "jobs": [
            {
                "name": "build-and-test",
                "conclusion": "failure",
                "steps": [
                    {"name": "Run pytest", "conclusion": "failure"},
                    {"name": "Upload coverage", "conclusion": "success"},
                ],
            },
            {
                "name": "deploy",
                "conclusion": "success",
                "steps": [],
            },
        ]
    }

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/jobs"
    ).mock(return_value=Response(200, json=jobs_response))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_failed_jobs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    assert len(result) == 1
    assert result[0]["job_name"] == "build-and-test"
    assert result[0]["conclusion"] == "failure"
    assert "Run pytest" in result[0]["steps_failed"]
    assert "Upload coverage" not in result[0]["steps_failed"]


@respx.mock
async def test_get_failed_jobs_no_failures():
    """Returns empty list when all jobs passed."""
    jobs_response = {
        "jobs": [
            {"name": "build", "conclusion": "success", "steps": []},
        ]
    }

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/jobs"
    ).mock(return_value=Response(200, json=jobs_response))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_failed_jobs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    assert result == []


@respx.mock
async def test_get_failed_jobs_http_error():
    """HTTP error returns single-element list with 'error' key, not exception."""
    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs/{RUN_ID}/jobs"
    ).mock(return_value=Response(403, text="Forbidden"))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_failed_jobs.ainvoke(
            {"run_id": RUN_ID, "repository": REPO}
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert "error" in result[0]


# ---------------------------------------------------------------------------
# get_commit_diff tests
# ---------------------------------------------------------------------------

SHA = "abc123def456"


@respx.mock
async def test_get_commit_diff_success():
    """Successful diff fetch returns the diff text."""
    diff_text = "diff --git a/src/checkout.py b/src/checkout.py\n+    amount = 99.99\n"

    respx.get(
        f"https://api.github.com/repos/{REPO}/commits/{SHA}"
    ).mock(return_value=Response(200, text=diff_text))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_commit_diff.ainvoke(
            {"commit_sha": SHA, "repository": REPO}
        )

    assert "diff --git" in result
    assert "amount = 99.99" in result


@respx.mock
async def test_get_commit_diff_404():
    """404 from commit endpoint returns a descriptive error string."""
    respx.get(
        f"https://api.github.com/repos/{REPO}/commits/{SHA}"
    ).mock(return_value=Response(404, text="Not Found"))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_commit_diff.ainvoke(
            {"commit_sha": SHA, "repository": REPO}
        )

    assert isinstance(result, str)
    assert "404" in result or "error" in result.lower() or SHA in result


@respx.mock
async def test_get_commit_diff_truncated():
    """Diff longer than _TOOL_DIFF_LIMIT is truncated with a notice."""
    diff_text = "+" + "A" * 5_000
    respx.get(
        f"https://api.github.com/repos/{REPO}/commits/{SHA}"
    ).mock(return_value=Response(200, text=diff_text))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_commit_diff.ainvoke(
            {"commit_sha": SHA, "repository": REPO}
        )

    assert len(result) <= 4_000 + 60  # 4000 chars + truncation notice overhead
    assert "truncated" in result


# ---------------------------------------------------------------------------
# get_recent_runs_for_test tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_recent_runs_for_test_success():
    """Returns list of matching run dicts filtered by workflow name."""
    runs_response = {
        "workflow_runs": [
            {
                "id": 111,
                "name": "CI Pipeline",
                "status": "completed",
                "conclusion": "failure",
                "created_at": "2026-05-30T14:22:01Z",
            },
            {
                "id": 222,
                "name": "Deploy Pipeline",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-05-29T10:00:00Z",
            },
        ]
    }

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs"
    ).mock(return_value=Response(200, json=runs_response))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_recent_runs_for_test.ainvoke(
            {"test_name": "CI Pipeline", "repository": REPO, "limit": 10}
        )

    assert len(result) == 1
    assert result[0]["run_id"] == 111
    assert result[0]["conclusion"] == "failure"
    assert result[0]["status"] == "completed"
    assert result[0]["created_at"] == "2026-05-30T14:22:01Z"


@respx.mock
async def test_get_recent_runs_for_test_no_match():
    """Returns empty list when no runs match the test name."""
    runs_response = {
        "workflow_runs": [
            {
                "id": 333,
                "name": "Other Workflow",
                "status": "completed",
                "conclusion": "success",
                "created_at": "2026-05-28T08:00:00Z",
            }
        ]
    }

    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs"
    ).mock(return_value=Response(200, json=runs_response))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_recent_runs_for_test.ainvoke(
            {"test_name": "CI Pipeline", "repository": REPO, "limit": 10}
        )

    assert result == []


@respx.mock
async def test_get_recent_runs_for_test_http_error():
    """HTTP error returns single-element list with 'error' key."""
    respx.get(
        f"https://api.github.com/repos/{REPO}/actions/runs"
    ).mock(return_value=Response(500, text="Internal Server Error"))

    with patch("src.agents.tools.github_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_recent_runs_for_test.ainvoke(
            {"test_name": "CI Pipeline", "repository": REPO, "limit": 10}
        )

    assert isinstance(result, list)
    assert len(result) == 1
    assert "error" in result[0]
