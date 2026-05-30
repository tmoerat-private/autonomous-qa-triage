from __future__ import annotations

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger(__name__)

_RETRY_EXCEPTIONS = (httpx.TransportError, httpx.TimeoutException)

_CHAT_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackClient:
    """Stateless Slack Web API client.

    Unlike the Jira client this class does *not* act as a context manager.
    Each method opens (and closes) its own ``httpx.AsyncClient`` so the
    caller does not need to manage connection lifecycle.

    Usage::

        client = SlackClient(settings)
        await client.post_message(
            channel_id="C012AB3CD",
            blocks=[...],
            text="Fallback text",
        )
    """

    def __init__(self, settings) -> None:  # noqa: ANN001
        self._settings = settings

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def post_message(
        self,
        channel_id: str,
        blocks: list[dict],
        text: str,
    ) -> dict:
        """Post a Block Kit message to a Slack channel.

        The method uses ``text`` as both the notification fallback and the
        plain-text summary displayed in environments that cannot render blocks.

        Args:
            channel_id: Slack channel ID (e.g. ``"C012AB3CD"``).
            blocks: Slack Block Kit block list.
            text: Plain-text fallback / notification text.

        Returns:
            Parsed Slack API JSON response dict.  Check ``data["ok"]`` for
            success; ``data["error"]`` contains the error code on failure.

        Raises:
            httpx.HTTPStatusError: On non-2xx HTTP responses (after retries).
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                _CHAT_POST_MESSAGE_URL,
                headers={
                    "Authorization": f"Bearer {self._settings.slack_bot_token}",
                },
                json={
                    "channel": channel_id,
                    "text": text,
                    "blocks": blocks,
                },
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("ok"):
                logger.warning(
                    "slack.post_message.api_error",
                    error=data.get("error"),
                    channel_id=channel_id,
                )
            else:
                logger.info(
                    "slack.post_message.sent",
                    channel_id=channel_id,
                    ts=data.get("ts"),
                )

            return data
