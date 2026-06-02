"""Tests for slack_tools.py — respx mocks for all outbound httpx calls."""
from __future__ import annotations

from unittest.mock import patch

import pytest
import respx
from httpx import Response

from src.agents.tools.slack_tools import (
    post_failure_notification,
    post_thread_reply,
    update_notification_with_ticket,
)
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Shared settings mock
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(
    slack_bot_token="xoxb-test-token",
    slack_channel_id="C012AB3CD",
    anthropic_api_key="test-key",
)

_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_UPDATE_URL = "https://slack.com/api/chat.update"

# ---------------------------------------------------------------------------
# post_failure_notification tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_post_failure_notification_success():
    """Successful post returns the message ts string."""
    respx.post(_POST_MESSAGE_URL).mock(
        return_value=Response(200, json={"ok": True, "ts": "12345.6789"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        ts = await post_failure_notification.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "failure_summary": {
                    "test_name": "tests/test_checkout.py::test_payment",
                    "category": "product_bug",
                    "confidence": 0.92,
                    "is_duplicate": False,
                    "reasoning": "Assertion error in checkout logic",
                    "repository": "acme/backend",
                    "branch": "main",
                },
            }
        )

    assert ts == "12345.6789"


@respx.mock
async def test_post_failure_notification_slack_error():
    """Slack API error response (ok=false) returns empty string."""
    respx.post(_POST_MESSAGE_URL).mock(
        return_value=Response(200, json={"ok": False, "error": "channel_not_found"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        ts = await post_failure_notification.ainvoke(
            {
                "channel_id": "CINVALID",
                "failure_summary": {
                    "test_name": "tests/test_foo.py::test_bar",
                    "category": "product_bug",
                    "confidence": 0.80,
                    "is_duplicate": False,
                },
            }
        )

    # Should return empty string (falsy) on Slack API error
    assert not ts


@respx.mock
async def test_post_failure_notification_falls_back_to_settings_channel():
    """Empty channel_id falls back to settings.slack_channel_id."""
    respx.post(_POST_MESSAGE_URL).mock(
        return_value=Response(200, json={"ok": True, "ts": "99999.0000"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        ts = await post_failure_notification.ainvoke(
            {
                "channel_id": "",  # empty — should fall back to settings
                "failure_summary": {
                    "test_name": "tests/test_auth.py::test_login",
                    "category": "flaky",
                    "confidence": 0.55,
                    "is_duplicate": True,
                },
            }
        )

    assert ts == "99999.0000"


@respx.mock
async def test_post_failure_notification_http_error():
    """Non-2xx HTTP response returns empty string, not an exception."""
    respx.post(_POST_MESSAGE_URL).mock(return_value=Response(503, text="Service Unavailable"))

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        ts = await post_failure_notification.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "failure_summary": {
                    "test_name": "tests/test_db.py::test_connect",
                    "category": "env_issue",
                    "confidence": 0.88,
                    "is_duplicate": False,
                },
            }
        )

    assert not ts


# ---------------------------------------------------------------------------
# post_thread_reply tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_post_thread_reply_success():
    """Successful reply returns True."""
    respx.post(_POST_MESSAGE_URL).mock(
        return_value=Response(200, json={"ok": True, "ts": "12346.0000"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await post_thread_reply.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "thread_ts": "12345.6789",
                "message": "Jira ticket PROJ-42 has been created.",
            }
        )

    assert result is True


@respx.mock
async def test_post_thread_reply_slack_error():
    """Slack API error response returns False."""
    respx.post(_POST_MESSAGE_URL).mock(
        return_value=Response(200, json={"ok": False, "error": "not_in_channel"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await post_thread_reply.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "thread_ts": "12345.6789",
                "message": "Follow-up message",
            }
        )

    assert result is False


@respx.mock
async def test_post_thread_reply_http_error():
    """Non-2xx HTTP response returns False, not an exception."""
    respx.post(_POST_MESSAGE_URL).mock(return_value=Response(500, text="Error"))

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await post_thread_reply.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "thread_ts": "12345.6789",
                "message": "Some reply",
            }
        )

    assert result is False


# ---------------------------------------------------------------------------
# update_notification_with_ticket tests
# ---------------------------------------------------------------------------


@respx.mock
async def test_update_notification_with_ticket_success():
    """Successful update returns True."""
    respx.post(_UPDATE_URL).mock(
        return_value=Response(200, json={"ok": True, "ts": "12345.6789"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await update_notification_with_ticket.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "message_ts": "12345.6789",
                "ticket_url": "https://test.atlassian.net/browse/PROJ-42",
            }
        )

    assert result is True


@respx.mock
async def test_update_notification_with_ticket_slack_error():
    """Slack API error response returns False."""
    respx.post(_UPDATE_URL).mock(
        return_value=Response(200, json={"ok": False, "error": "message_not_found"})
    )

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await update_notification_with_ticket.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "message_ts": "00000.0000",
                "ticket_url": "https://test.atlassian.net/browse/PROJ-99",
            }
        )

    assert result is False


@respx.mock
async def test_update_notification_with_ticket_http_error():
    """Non-2xx HTTP response returns False, not an exception."""
    respx.post(_UPDATE_URL).mock(return_value=Response(503, text="Service Unavailable"))

    with patch("src.agents.tools.slack_tools.get_settings", return_value=_MOCK_SETTINGS):
        result = await update_notification_with_ticket.ainvoke(
            {
                "channel_id": "C012AB3CD",
                "message_ts": "12345.6789",
                "ticket_url": "https://test.atlassian.net/browse/PROJ-1",
            }
        )

    assert result is False
