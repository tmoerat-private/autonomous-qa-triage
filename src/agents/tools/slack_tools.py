"""LangChain tools for Slack API operations.

Each tool wraps the SlackClient integration and the message_builder helpers,
handles errors gracefully, and returns primitive values so agent nodes never
need to catch exceptions or parse raw Slack API responses.

Tool list
---------
- post_failure_notification         Post a Block Kit triage summary to a channel.
- post_thread_reply                 Post a follow-up message in an existing thread.
- update_notification_with_ticket   Update a posted message to add a Jira link.
"""

from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool

from src.config.settings import get_settings
from src.integrations.slack.client import SlackClient
from src.integrations.slack.message_builder import build_triage_notification

logger = structlog.get_logger(__name__)

_SLACK_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"
_SLACK_CHAT_UPDATE_URL = "https://slack.com/api/chat.update"


# ---------------------------------------------------------------------------
# Tool 1: post_failure_notification
# ---------------------------------------------------------------------------


@tool
async def post_failure_notification(
    channel_id: str,
    failure_summary: dict,
) -> str:
    """Post a Block Kit formatted triage notification to a Slack channel.

    Builds a rich Block Kit message from the triage result fields contained in
    ``failure_summary`` and dispatches it via ``SlackClient.post_message``.

    Expected keys in ``failure_summary``
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Required:

    - ``test_name`` (str): Fully-qualified test identifier.
    - ``category`` (str): FailureCategory value, e.g. ``"product_bug"``.
    - ``confidence`` (float): Classification confidence in ``[0.0, 1.0]``.
    - ``is_duplicate`` (bool): Whether the failure matches a known signature.

    Optional (fall back to ``None`` / sensible defaults when absent):

    - ``reasoning`` (str | None): LLM reasoning narrative.
    - ``repository`` (str | None): Repository name.
    - ``branch`` (str | None): Git branch name.
    - ``ticket_url`` (str | None): Jira ticket URL.
    - ``ticket_key`` (str | None): Jira issue key, e.g. ``"QA-42"``.

    Args:
        channel_id: Slack channel ID to post to, e.g. ``"C012AB3CD"``.
            If empty, falls back to ``settings.slack_channel_id``.
        failure_summary: Dict of triage result fields (see above).

    Returns:
        The Slack message timestamp (``ts``) string on success, which serves
        as the thread identifier for follow-up replies.  Returns an empty
        string ``""`` on failure.
    """
    settings = get_settings()
    effective_channel = channel_id or settings.slack_channel_id

    payload = build_triage_notification(
        test_name=failure_summary.get("test_name", "unknown"),
        category=failure_summary.get("category", "unknown"),
        confidence=float(failure_summary.get("confidence", 0.0)),
        reasoning=failure_summary.get("reasoning"),
        repository=failure_summary.get("repository"),
        branch=failure_summary.get("branch"),
        ticket_url=failure_summary.get("ticket_url"),
        ticket_key=failure_summary.get("ticket_key"),
        is_duplicate=bool(failure_summary.get("is_duplicate", False)),
    )

    try:
        client = SlackClient(settings)
        data = await client.post_message(
            channel_id=effective_channel,
            blocks=payload["blocks"],
            text=payload["text"],
        )
    except Exception as exc:
        logger.error(
            "slack_tools.post_failure_notification.failed",
            channel_id=effective_channel,
            error=str(exc),
        )
        return ""

    if not data.get("ok"):
        logger.warning(
            "slack_tools.post_failure_notification.api_error",
            channel_id=effective_channel,
            slack_error=data.get("error"),
        )
        return ""

    ts: str = data.get("ts", "")
    logger.info(
        "slack_tools.post_failure_notification.success",
        channel_id=effective_channel,
        ts=ts,
    )
    return ts


# ---------------------------------------------------------------------------
# Tool 2: post_thread_reply
# ---------------------------------------------------------------------------


@tool
async def post_thread_reply(
    channel_id: str,
    thread_ts: str,
    message: str,
) -> bool:
    """Post a plain-text follow-up message in an existing Slack thread.

    Uses ``chat.postMessage`` with the ``thread_ts`` field set so that the
    reply is nested under the original notification rather than posted as a
    new top-level message.  This keeps the triage timeline easy to follow
    inside Slack without cluttering the channel feed.

    Args:
        channel_id: Slack channel ID that contains the thread, e.g.
            ``"C012AB3CD"``.
        thread_ts: The timestamp of the parent message (returned by
            ``post_failure_notification`` as its ``ts`` value).
        message: Plain-text content for the reply.  Slack mrkdwn formatting
            is supported, e.g. ``"*Ticket created:* <https://...>"``.

    Returns:
        ``True`` if the reply was posted successfully, ``False`` otherwise.
    """
    settings = get_settings()

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                _SLACK_CHAT_POST_MESSAGE_URL,
                headers={
                    "Authorization": f"Bearer {settings.slack_bot_token}",
                },
                json={
                    "channel": channel_id,
                    "thread_ts": thread_ts,
                    "text": message,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.error(
            "slack_tools.post_thread_reply.failed",
            channel_id=channel_id,
            thread_ts=thread_ts,
            error=str(exc),
        )
        return False

    if not data.get("ok"):
        logger.warning(
            "slack_tools.post_thread_reply.api_error",
            channel_id=channel_id,
            thread_ts=thread_ts,
            slack_error=data.get("error"),
        )
        return False

    logger.info(
        "slack_tools.post_thread_reply.success",
        channel_id=channel_id,
        thread_ts=thread_ts,
    )
    return True


# ---------------------------------------------------------------------------
# Tool 3: update_notification_with_ticket
# ---------------------------------------------------------------------------


@tool
async def update_notification_with_ticket(
    channel_id: str,
    message_ts: str,
    ticket_url: str,
) -> bool:
    """Update an existing Slack message to append a Jira ticket link button.

    After the ``ticket_creator`` node creates a Jira issue, the notifier node
    calls this tool to update the already-posted Slack message so the team
    can navigate directly to the ticket without scrolling past the thread.

    The update fetches the original message's blocks from the Slack API and
    appends (or replaces) a trailing actions block that contains a "View
    Ticket" button pointing to ``ticket_url``.  The plain-text fallback
    (``text``) is also updated to include the URL.

    Implementation note: Slack's ``chat.update`` endpoint requires the full
    updated block list; partial updates are not supported.  To keep things
    predictable this tool builds a minimal two-block message (header + button)
    rather than attempting to re-fetch and patch the original blocks, which
    avoids a second round-trip and eliminates races with concurrent updates.

    Slack API: ``POST /api/chat.update``

    Args:
        channel_id: Slack channel ID that contains the message.
        message_ts: The ``ts`` of the message to update (returned by
            ``post_failure_notification``).
        ticket_url: Full URL to the Jira ticket, e.g.
            ``"https://company.atlassian.net/browse/QA-42"``.

    Returns:
        ``True`` if the message was updated successfully, ``False`` otherwise.
    """
    settings = get_settings()

    updated_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Jira ticket created:* <{ticket_url}|View Ticket>",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {
                        "type": "plain_text",
                        "text": "View Jira Ticket",
                        "emoji": True,
                    },
                    "url": ticket_url,
                    "action_id": "view_ticket",
                }
            ],
        },
    ]

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.post(
                _SLACK_CHAT_UPDATE_URL,
                headers={
                    "Authorization": f"Bearer {settings.slack_bot_token}",
                },
                json={
                    "channel": channel_id,
                    "ts": message_ts,
                    "text": f"Jira ticket created: {ticket_url}",
                    "blocks": updated_blocks,
                },
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.error(
            "slack_tools.update_notification_with_ticket.failed",
            channel_id=channel_id,
            message_ts=message_ts,
            ticket_url=ticket_url,
            error=str(exc),
        )
        return False

    if not data.get("ok"):
        logger.warning(
            "slack_tools.update_notification_with_ticket.api_error",
            channel_id=channel_id,
            message_ts=message_ts,
            slack_error=data.get("error"),
        )
        return False

    logger.info(
        "slack_tools.update_notification_with_ticket.success",
        channel_id=channel_id,
        message_ts=message_ts,
        ticket_url=ticket_url,
    )
    return True
