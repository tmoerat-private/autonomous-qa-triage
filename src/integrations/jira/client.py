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


class JiraClient:
    """Async Jira REST API v3 client.

    Must be used as an async context manager so the underlying
    ``httpx.AsyncClient`` is properly initialised and torn down::

        async with JiraClient(settings) as client:
            issue = await client.create_issue(
                summary="Test failure: test_login",
                description="...",
                priority="High",
            )
    """

    def __init__(self, settings) -> None:
        self._settings = settings
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> JiraClient:
        self._client = httpx.AsyncClient(
            base_url=self._settings.jira_url,
            auth=httpx.BasicAuth(
                self._settings.jira_email,
                self._settings.jira_api_token,
            ),
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )
        return self

    async def __aexit__(self, *args) -> None:
        if self._client is not None:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                "JiraClient not initialized — use as async context manager"
            )
        return self._client

    # ------------------------------------------------------------------
    # API methods
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def create_issue(
        self,
        summary: str,
        description: str,
        priority: str,
        labels: list[str] | None = None,
    ) -> dict:
        """Create a Jira Bug issue and return its id, key, and browse URL.

        Args:
            summary: One-line issue title.
            description: Plain-text body (wrapped in Jira ADF paragraph node).
            priority: Jira priority name, e.g. ``"High"``, ``"Critical"``.
            labels: Optional list of labels; defaults to
                ``["autonomous-qa", "test-failure"]``.

        Returns:
            ``{"id": str, "key": str, "url": str}``

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        body = {
            "fields": {
                "project": {"key": self._settings.jira_project_key},
                "summary": summary,
                "description": {
                    "type": "doc",
                    "version": 1,
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": description}],
                        }
                    ],
                },
                "issuetype": {"name": "Bug"},
                "priority": {"name": priority},
                "labels": labels or ["autonomous-qa", "test-failure"],
            }
        }

        logger.info(
            "jira.create_issue.request",
            summary=summary,
            priority=priority,
            project_key=self._settings.jira_project_key,
        )

        try:
            response = await self.client.post("/rest/api/3/issue", json=body)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "jira.create_issue.http_error",
                status_code=exc.response.status_code,
                response_text=exc.response.text,
            )
            raise

        data = response.json()
        result = {
            "id": data["id"],
            "key": data["key"],
            "url": f"{self._settings.jira_url}/browse/{data['key']}",
        }

        logger.info(
            "jira.create_issue.response",
            issue_id=result["id"],
            issue_key=result["key"],
            url=result["url"],
        )

        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_issue(self, issue_key: str) -> dict:
        """Fetch a Jira issue by its key (e.g. ``"QA-42"``).

        Args:
            issue_key: The Jira issue key.

        Returns:
            Parsed JSON response as a plain ``dict``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        logger.info("jira.get_issue.request", issue_key=issue_key)
        response = await self.client.get(f"/rest/api/3/issue/{issue_key}")
        response.raise_for_status()
        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def add_comment(self, issue_key: str, comment: str) -> dict:
        """Append a plain-text comment to an existing Jira issue.

        Args:
            issue_key: The Jira issue key to comment on.
            comment: Plain-text comment body.

        Returns:
            Parsed JSON response for the created comment.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [{"type": "text", "text": comment}],
                    }
                ],
            }
        }

        response = await self.client.post(
            f"/rest/api/3/issue/{issue_key}/comment",
            json=body,
        )
        response.raise_for_status()
        return response.json()
