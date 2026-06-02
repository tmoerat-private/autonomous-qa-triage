"""Tests for jenkins_tools.py — respx mocks for all outbound httpx calls."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import Response

from src.agents.tools.jenkins_tools import (
    get_build_console_log,
    get_build_parameters,
    get_build_test_report,
    trigger_build_rerun,
)
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Shared settings mock
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(
    jenkins_url="https://jenkins.example.com",
    jenkins_user="admin",
    jenkins_token="test-token",
    anthropic_api_key="test-key",
)

JOB = "my-pipeline"
BUILD = 42


# ---------------------------------------------------------------------------
# get_build_console_log tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_build_console_log_success():
    """Successful fetch returns the console log text."""
    log_text = "BUILD FAILURE\njava.lang.NullPointerException at MyClass.java:42\n"

    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/consoleText"
    ).mock(return_value=Response(200, text=log_text))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_console_log.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert "NullPointerException" in result
    assert "BUILD FAILURE" in result


@respx.mock
async def test_get_build_console_log_truncated():
    """Logs longer than _TOOL_LOG_LIMIT (8 000 chars) are truncated with a notice."""
    long_log = "X" * 10_000

    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/consoleText"
    ).mock(return_value=Response(200, text=long_log))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_console_log.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    # 8000 chars + truncation notice (about 45 chars)
    assert len(result) <= 8_100
    assert "truncated" in result.lower() or "8 000" in result


@respx.mock
async def test_get_build_console_log_http_error():
    """HTTP error returns a descriptive error string, not an exception."""
    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/consoleText"
    ).mock(return_value=Response(404, text="Not Found"))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_console_log.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert isinstance(result, str)
    assert "404" in result or "Error" in result


# ---------------------------------------------------------------------------
# get_build_test_report tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_build_test_report_success():
    """Successful fetch returns total, failed, and cases fields."""
    report_json = {
        "totalCount": 5,
        "failCount": 2,
        "suites": [
            {
                "cases": [
                    {
                        "className": "tests.auth.TestLogin",
                        "name": "test_user_login",
                        "status": "FAILED",
                        "errorDetails": "AssertionError: expected 200 got 500",
                    },
                    {
                        "className": "tests.auth.TestLogin",
                        "name": "test_user_logout",
                        "status": "PASSED",
                        "errorDetails": None,
                    },
                ]
            }
        ],
    }

    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/testReport/api/json"
    ).mock(return_value=Response(200, json=report_json))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_test_report.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result["total"] == 5
    assert result["failed"] == 2
    assert len(result["cases"]) == 2

    failed_case = next(c for c in result["cases"] if c["status"] == "FAILED")
    assert "test_user_login" in failed_case["name"]
    assert "AssertionError" in failed_case["error"]


@respx.mock
async def test_get_build_test_report_404():
    """404 (no test report exists) returns a dict with 'error' key, not an exception."""
    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/testReport/api/json"
    ).mock(return_value=Response(404, text="Not Found"))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_test_report.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert "error" in result
    assert "404" in result["error"]


# ---------------------------------------------------------------------------
# get_build_parameters tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_build_parameters_success():
    """Returns a flat dict of parameter name-value pairs."""
    build_json = {
        "actions": [
            {
                "_class": "hudson.model.ParametersAction",
                "parameters": [
                    {"name": "BRANCH", "value": "main"},
                    {"name": "COMMIT_SHA", "value": "abc123"},
                    {"name": "DEPLOY_ENV", "value": "staging"},
                ],
            },
            {
                "_class": "hudson.model.CauseAction",
            },
        ]
    }

    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/api/json"
    ).mock(return_value=Response(200, json=build_json))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_parameters.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result["BRANCH"] == "main"
    assert result["COMMIT_SHA"] == "abc123"
    assert result["DEPLOY_ENV"] == "staging"


@respx.mock
async def test_get_build_parameters_no_params_action():
    """Build with no ParametersAction returns an empty dict."""
    build_json = {
        "actions": [
            {"_class": "hudson.model.CauseAction"},
        ]
    }

    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/api/json"
    ).mock(return_value=Response(200, json=build_json))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_parameters.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result == {}


@respx.mock
async def test_get_build_parameters_http_error():
    """HTTP error returns dict with 'error' key, not an exception."""
    respx.get(
        f"https://jenkins.example.com/job/{JOB}/{BUILD}/api/json"
    ).mock(return_value=Response(500, text="Internal Server Error"))

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_build_parameters.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert "error" in result


# ---------------------------------------------------------------------------
# trigger_build_rerun tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_trigger_build_rerun_success():
    """Successful trigger returns triggered=True and the new build URL."""
    crumb_response = {
        "crumbRequestField": "Jenkins-Crumb",
        "crumb": "test-crumb-value",
    }

    respx.get("https://jenkins.example.com/crumbIssuer/api/json").mock(
        return_value=Response(200, json=crumb_response)
    )
    respx.post("https://jenkins.example.com/job/my-pipeline/build").mock(
        return_value=Response(
            201,
            headers={"Location": "https://jenkins.example.com/queue/item/99/"},
            text="",
        )
    )

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await trigger_build_rerun.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result["triggered"] is True
    assert result["new_build_url"] == "https://jenkins.example.com/queue/item/99/"


@respx.mock
async def test_trigger_build_rerun_no_crumb():
    """When crumb endpoint returns 404, build is still triggered without crumb."""
    respx.get("https://jenkins.example.com/crumbIssuer/api/json").mock(
        return_value=Response(404, text="Not Found")
    )
    respx.post("https://jenkins.example.com/job/my-pipeline/build").mock(
        return_value=Response(
            201,
            headers={"Location": "https://jenkins.example.com/queue/item/100/"},
            text="",
        )
    )

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await trigger_build_rerun.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result["triggered"] is True


@respx.mock
async def test_trigger_build_rerun_http_error():
    """Failed POST returns triggered=False with an error description."""
    respx.get("https://jenkins.example.com/crumbIssuer/api/json").mock(
        return_value=Response(200, json={"crumbRequestField": "Jenkins-Crumb", "crumb": "x"})
    )
    respx.post("https://jenkins.example.com/job/my-pipeline/build").mock(
        return_value=Response(403, text="Forbidden")
    )

    with patch("src.agents.tools.jenkins_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await trigger_build_rerun.ainvoke(
            {"job_name": JOB, "build_number": BUILD}
        )

    assert result["triggered"] is False
    assert "error" in result
