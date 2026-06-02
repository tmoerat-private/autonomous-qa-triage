"""Tests for jira_tools.py — respx mocks for all outbound httpx calls."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import Response

from src.agents.tools.jira_tools import (
    create_jira_ticket,
    get_ticket_status,
    search_similar_tickets,
)
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Shared settings mock
# JiraClient uses settings.jira_url as the httpx base_url.
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(
    jira_url="https://test.atlassian.net",
    jira_email="test@example.com",
    jira_api_token="test-api-token",
    jira_project_key="PROJ",
    anthropic_api_key="test-key",
)

# ---------------------------------------------------------------------------
# create_jira_ticket tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_create_jira_ticket_success():
    """Successful ticket creation returns ticket_id and url."""
    respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(
            201,
            json={"id": "10001", "key": "PROJ-1", "self": "https://test.atlassian.net/rest/api/3/issue/10001"},
        )
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await create_jira_ticket.ainvoke(
            {
                "title": "test_login fails with AssertionError",
                "description": "Assertion error in checkout flow",
                "priority": "High",
                "labels": ["autonomous-qa"],
            }
        )

    assert result["ticket_id"] == "PROJ-1"
    assert "PROJ-1" in result["url"]
    assert "error" not in result


@respx.mock
async def test_create_jira_ticket_error():
    """HTTP 500 from Jira returns an error dict, not an exception."""
    respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await create_jira_ticket.ainvoke(
            {
                "title": "test_login fails",
                "description": "Something went wrong",
                "priority": "Medium",
                "labels": [],
            }
        )

    assert result["ticket_id"] is None
    assert result["url"] is None
    assert "error" in result


@respx.mock
async def test_create_jira_ticket_401_returns_error_dict():
    """HTTP 401 (auth failure) returns error dict with ticket_id=None."""
    respx.post("https://test.atlassian.net/rest/api/3/issue").mock(
        return_value=Response(401, text="Unauthorized")
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await create_jira_ticket.ainvoke(
            {
                "title": "test_auth fails",
                "description": "Auth issue",
                "priority": "Low",
                "labels": [],
            }
        )

    assert result["ticket_id"] is None
    assert "error" in result


# ---------------------------------------------------------------------------
# get_ticket_status tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_ticket_status_success():
    """Returns status, assignee, and resolution for an existing ticket."""
    issue_json = {
        "key": "PROJ-42",
        "fields": {
            "status": {"name": "In Progress"},
            "assignee": {"displayName": "Jane Smith", "emailAddress": "jane@example.com"},
            "resolution": {"name": "None"},
        },
    }

    respx.get("https://test.atlassian.net/rest/api/3/issue/PROJ-42").mock(
        return_value=Response(200, json=issue_json)
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_ticket_status.ainvoke({"ticket_id": "PROJ-42"})

    assert result["status"] == "In Progress"
    assert result["assignee"] == "Jane Smith"
    assert "error" not in result


@respx.mock
async def test_get_ticket_status_unassigned():
    """Ticket with no assignee returns 'Unassigned' string."""
    issue_json = {
        "key": "PROJ-43",
        "fields": {
            "status": {"name": "Open"},
            "assignee": None,
            "resolution": None,
        },
    }

    respx.get("https://test.atlassian.net/rest/api/3/issue/PROJ-43").mock(
        return_value=Response(200, json=issue_json)
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_ticket_status.ainvoke({"ticket_id": "PROJ-43"})

    assert result["assignee"] == "Unassigned"
    assert result["resolution"] == "None"


@respx.mock
async def test_get_ticket_status_404():
    """404 for unknown ticket returns error dict with 'unknown' values, not exception."""
    respx.get("https://test.atlassian.net/rest/api/3/issue/PROJ-999").mock(
        return_value=Response(404, text="Issue Does Not Exist")
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await get_ticket_status.ainvoke({"ticket_id": "PROJ-999"})

    assert result["status"] == "unknown"
    assert result["assignee"] == "unknown"
    assert result["resolution"] == "unknown"
    assert "error" in result


# ---------------------------------------------------------------------------
# search_similar_tickets tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_search_similar_tickets_success():
    """Returns list of matching ticket dicts from JQL search."""
    search_json = {
        "issues": [
            {
                "key": "PROJ-10",
                "fields": {
                    "summary": "test_login fails with AssertionError",
                    "status": {"name": "Open"},
                },
            },
            {
                "key": "PROJ-5",
                "fields": {
                    "summary": "AssertionError in auth flow",
                    "status": {"name": "In Progress"},
                },
            },
        ]
    }

    respx.get("https://test.atlassian.net/rest/api/3/search").mock(
        return_value=Response(200, json=search_json)
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await search_similar_tickets.ainvoke(
            {
                "error_signature": "AssertionError: expected True",
                "project_key": "PROJ",
                "limit": 5,
            }
        )

    assert len(result) == 2
    keys = [t["id"] for t in result]
    assert "PROJ-10" in keys
    assert "PROJ-5" in keys

    ticket = next(t for t in result if t["id"] == "PROJ-10")
    assert "test_login" in ticket["title"]
    assert "PROJ-10" in ticket["url"]


@respx.mock
async def test_search_similar_tickets_empty_results():
    """No matches returns empty list."""
    respx.get("https://test.atlassian.net/rest/api/3/search").mock(
        return_value=Response(200, json={"issues": []})
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await search_similar_tickets.ainvoke(
            {
                "error_signature": "some unique error",
                "project_key": "PROJ",
                "limit": 5,
            }
        )

    assert result == []


@respx.mock
async def test_search_similar_tickets_http_error():
    """HTTP error from Jira search returns empty list, not an exception."""
    respx.get("https://test.atlassian.net/rest/api/3/search").mock(
        return_value=Response(500, text="Internal Server Error")
    )

    with patch("src.agents.tools.jira_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await search_similar_tickets.ainvoke(
            {
                "error_signature": "AssertionError",
                "project_key": "PROJ",
                "limit": 5,
            }
        )

    assert result == []
