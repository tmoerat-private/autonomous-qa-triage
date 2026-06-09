from __future__ import annotations

import io
import zipfile

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.config.constants import MAX_LOG_LENGTH
from src.integrations.base import BaseCIClient

logger = structlog.get_logger(__name__)

_RETRY_EXCEPTIONS = (httpx.TransportError, httpx.TimeoutException)

# GitHub returns 404 or 410 when logs have expired or never existed.
_LOG_GONE_STATUSES = {404, 410}


class GitHubActionsClient(BaseCIClient):
    """Async GitHub Actions REST API client.

    Must be used as an async context manager so the underlying
    ``httpx.AsyncClient`` is properly initialised and torn down::

        async with GitHubActionsClient(settings) as client:
            details = await client.get_build_details("org/repo", 12345)
            logs = await client.get_build_logs("org/repo", 12345)

    Note: ``settings.github_app_id`` is treated as a personal-access token
    (PAT) for MVP simplicity.  Replace with GitHub App JWT authentication
    once the PAT approach is retired.
    """

    def __init__(self, settings) -> None:
        super().__init__(settings)
        self._headers: dict[str, str] = {
            "Authorization": f"token {settings.github_app_id}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    # ------------------------------------------------------------------
    # Context manager — creates a properly configured AsyncClient
    # ------------------------------------------------------------------

    async def __aenter__(self) -> GitHubActionsClient:
        self._client = httpx.AsyncClient(
            timeout=30.0,
            headers=self._headers,
            follow_redirects=True,
        )
        return self

    # __aexit__ is inherited from BaseCIClient and handles aclose() correctly.

    # ------------------------------------------------------------------
    # Concrete typed methods — satisfy the base-class abstract contract
    # by accepting a "repo::run_id" composite build_id for the single-arg
    # variants; the preferred public API uses explicit keyword arguments.
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_build_details(  # type: ignore[override]
        self,
        repo_full_name: str,
        run_id: int,
    ) -> dict:
        """Fetch workflow-run metadata from the GitHub Actions API.

        Args:
            repo_full_name: Repository in ``"org/repo"`` format.
            run_id: The numeric workflow-run ID.

        Returns:
            Parsed JSON response as a plain ``dict``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        url = f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}"

        logger.debug(
            "github_actions.get_build_details.request",
            repo=repo_full_name,
            run_id=run_id,
        )

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github_actions.get_build_details.http_error",
                repo=repo_full_name,
                run_id=run_id,
                status_code=exc.response.status_code,
            )
            raise

        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_build_logs(  # type: ignore[override]
        self,
        repo_full_name: str,
        run_id: int,
    ) -> str:
        """Fetch and extract all log text for a GitHub Actions workflow run.

        GitHub redirects the ``/logs`` endpoint to a zip archive download.
        With ``follow_redirects=True`` on the client, the full zip bytes are
        returned directly.  This method extracts every ``.txt`` file inside
        the archive and concatenates them with ``"\\n---\\n"`` as a separator.

        The result is truncated to ``MAX_LOG_LENGTH`` characters when the
        combined log text exceeds that limit.

        Args:
            repo_full_name: Repository in ``"org/repo"`` format.
            run_id: The numeric workflow-run ID.

        Returns:
            Plain-text log content, possibly truncated.  Returns ``""`` when
            logs have expired (HTTP 404/410) or any other error occurs.
        """
        url = f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}/logs"

        logger.debug(
            "github_actions.get_build_logs.request",
            repo=repo_full_name,
            run_id=run_id,
        )

        try:
            response = await self.client.get(url)
        except Exception as exc:
            logger.warning(
                "github_actions.get_build_logs.request_error",
                repo=repo_full_name,
                run_id=run_id,
                error=str(exc),
            )
            return ""

        if response.status_code in _LOG_GONE_STATUSES:
            logger.warning(
                "github_actions.get_build_logs.logs_unavailable",
                repo=repo_full_name,
                run_id=run_id,
                status_code=response.status_code,
            )
            return ""

        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github_actions.get_build_logs.http_error",
                repo=repo_full_name,
                run_id=run_id,
                status_code=exc.response.status_code,
            )
            return ""

        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
                all_txt = [n for n in zf.namelist() if n.endswith(".txt")]

                # Prioritise files that are most likely to contain test-failure
                # output (pytest FAILED/ERROR lines) so they appear early in the
                # concatenated text and survive the MAX_LOG_LENGTH truncation.
                # Files matching "test" or "pytest" (case-insensitive) come
                # first; job-level summary files (e.g. "2_Test.txt") come
                # second; everything else (set-up, infra steps) comes last.
                def _sort_key(name: str) -> int:
                    lower = name.lower()
                    if "pytest" in lower or "run pytest" in lower:
                        return 0
                    if "test" in lower and "/" not in name:
                        return 1  # top-level summary like "2_Test.txt"
                    if "test" in lower:
                        return 2
                    return 3

                ordered = sorted(all_txt, key=_sort_key)

                parts: list[str] = []
                for name in ordered:
                    with zf.open(name) as f:
                        parts.append(f.read().decode("utf-8", errors="replace"))
            text = "\n---\n".join(parts)
        except Exception as exc:
            logger.warning(
                "github_actions.get_build_logs.zip_error",
                repo=repo_full_name,
                run_id=run_id,
                error=str(exc),
            )
            return ""

        if len(text) > MAX_LOG_LENGTH:
            logger.warning(
                "github_actions.get_build_logs.truncated",
                repo=repo_full_name,
                run_id=run_id,
                original_length=len(text),
                truncated_to=MAX_LOG_LENGTH,
            )
            text = text[:MAX_LOG_LENGTH]

        return text

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_run_jobs(
        self,
        repo_full_name: str,
        run_id: int,
    ) -> list[dict]:
        """Fetch all jobs that belong to a given workflow run.

        Args:
            repo_full_name: Repository in ``"org/repo"`` format.
            run_id: The numeric workflow-run ID.

        Returns:
            List of job objects as returned by the GitHub API, or ``[]`` on
            non-2xx responses (after retries).

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        url = (
            f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}/jobs"
        )

        logger.debug(
            "github_actions.get_run_jobs.request",
            repo=repo_full_name,
            run_id=run_id,
        )

        try:
            response = await self.client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "github_actions.get_run_jobs.http_error",
                repo=repo_full_name,
                run_id=run_id,
                status_code=exc.response.status_code,
            )
            raise

        return response.json().get("jobs", [])

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def trigger_rerun(self, repo_full_name: str, run_id: int) -> dict:
        """Re-run all failed jobs in an existing GitHub Actions workflow run.

        Uses the POST /repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs
        endpoint (GitHub returns 201 Created on success).

        Args:
            repo_full_name: Repository in "org/repo" format.
            run_id: The numeric workflow-run ID to re-run.

        Returns:
            {"triggered": True, "repo": repo_full_name, "run_id": run_id}

        Raises:
            httpx.HTTPStatusError: On non-2xx responses after retries.
        """
        url = (
            f"https://api.github.com/repos/{repo_full_name}/actions/runs/{run_id}"
            "/rerun-failed-jobs"
        )

        logger.debug(
            "github_actions.trigger_rerun.request",
            repo=repo_full_name,
            run_id=run_id,
        )

        response = await self.client.post(url)
        if response.status_code not in {200, 201}:
            response.raise_for_status()

        logger.info(
            "github_actions.trigger_rerun.triggered",
            repo=repo_full_name,
            run_id=run_id,
            status_code=response.status_code,
        )

        return {"triggered": True, "repo": repo_full_name, "run_id": run_id}
