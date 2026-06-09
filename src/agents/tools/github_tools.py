"""LangChain tools wrapping the GitHub Actions REST API.

Each function is decorated with ``@tool`` so LangGraph agent nodes can
discover and invoke them.  Claude reads the docstrings to decide which tool
matches a given sub-task, so keep them precise and unambiguous.

All tools instantiate ``GitHubActionsClient`` from project settings and rely
on it as an async context manager.  HTTP errors are caught and returned as
descriptive strings rather than raised, so a single failed GitHub call never
crashes the enclosing agent graph.

Rate limiting (HTTP 429): direct-httpx calls and calls that propagate
``httpx.HTTPStatusError`` from the client layer are retried up to 4 times
with exponential back-off (2 s → 60 s).  Other HTTP errors surface
immediately.
"""

from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from src.config.settings import get_settings
from src.integrations.github_actions.client import GitHubActionsClient

logger = structlog.get_logger(__name__)

# Tighter truncation limits applied at the tool layer (below MAX_LOG_LENGTH)
# so that Claude's context window is not saturated by a single tool result.
_TOOL_LOG_LIMIT = 8_000
_TOOL_DIFF_LIMIT = 4_000

# ---------------------------------------------------------------------------
# Tenacity 429 retry helpers — shared across all tool functions in this module
# ---------------------------------------------------------------------------

_is_rate_limit = retry_if_exception(
    lambda exc: isinstance(exc, httpx.HTTPStatusError)
    and exc.response.status_code == 429
)


def _before_sleep_log(retry_state) -> None:  # type: ignore[type-arg]
    """Log a structured warning before each tenacity sleep."""
    exc = retry_state.outcome.exception()
    url = "unknown"
    if isinstance(exc, httpx.HTTPStatusError) and exc.request is not None:
        url = str(exc.request.url)
    wait_secs = getattr(getattr(retry_state, "next_action", None), "sleep", 0.0)
    logger.warning(
        "github_tools.rate_limited.retrying",
        attempt=retry_state.attempt_number,
        url=url,
        wait_seconds=round(wait_secs, 2),
    )


def _retry_on_429() -> dict:
    """Return tenacity decorator kwargs for 429-only retry with back-off."""
    return dict(
        retry=_is_rate_limit,
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(4),
        before_sleep=_before_sleep_log,
    )


@tool
async def get_workflow_run_logs(run_id: int, repository: str) -> str:
    """Fetch the full console log text for a GitHub Actions workflow run.

    Use this tool when you need the raw output of a CI run to identify error
    messages, failing commands, or unexpected output that cannot be inferred
    from structured job/step metadata alone.

    The log is extracted from the zip archive that GitHub returns and
    returned as plain text.  Multiple job logs are joined with ``"\\n---\\n"``.
    The result is truncated to 8 000 characters when longer.

    Args:
        run_id: The numeric GitHub Actions workflow-run ID (e.g. 12345678).
        repository: Full repository name in ``"owner/repo"`` format
            (e.g. ``"acme-corp/backend"``).

    Returns:
        Plain-text log content, truncated to 8 000 chars.  Returns a
        descriptive error string on HTTP 4xx/5xx or when logs have expired.
    """
    settings = get_settings()

    logger.debug(
        "github_tools.get_workflow_run_logs.start",
        run_id=run_id,
        repository=repository,
    )

    # Note: GitHubActionsClient.get_build_logs catches HTTPStatusError
    # internally (including 429) and returns "" rather than re-raising, so
    # tenacity retry cannot be applied at this layer without modifying the
    # client.  The client's own retry handles transient transport errors.
    try:
        async with GitHubActionsClient(settings) as client:
            logs = await client.get_build_logs(repository, run_id)
    except Exception as exc:
        error_msg = f"Failed to fetch logs for run {run_id} in {repository}: {exc}"
        logger.warning("github_tools.get_workflow_run_logs.error", error=str(exc))
        return error_msg

    if not logs:
        return (
            f"No logs available for run {run_id} in {repository}. "
            "Logs may have expired (GitHub retains them for 90 days)."
        )

    if len(logs) > _TOOL_LOG_LIMIT:
        logs = logs[:_TOOL_LOG_LIMIT]
        logs += f"\n[... truncated to {_TOOL_LOG_LIMIT} characters ...]"

    return logs


@tool
async def get_failed_jobs(run_id: int, repository: str) -> list[dict]:
    """Return the failed jobs and their failed step names for a GitHub Actions run.

    Use this tool to quickly identify *which* jobs and steps failed without
    downloading the full log text.  The result is suitable for constructing a
    structured failure summary or for deciding which log sections to fetch next.

    Args:
        run_id: The numeric GitHub Actions workflow-run ID.
        repository: Full repository name in ``"owner/repo"`` format.

    Returns:
        A list of dicts, one per failed or cancelled job::

            [
                {
                    "job_name": "build-and-test",
                    "conclusion": "failure",
                    "steps_failed": ["Run pytest", "Upload coverage"],
                },
                ...
            ]

        Returns an empty list when the run has no failed jobs.  Returns a
        single-element list containing ``{"error": "<message>"}`` on HTTP
        4xx/5xx so that callers receive a uniform list type regardless of
        outcome.
    """
    settings = get_settings()

    logger.debug(
        "github_tools.get_failed_jobs.start",
        run_id=run_id,
        repository=repository,
    )

    @retry(**_retry_on_429())
    async def _fetch_jobs() -> list[dict]:
        # get_run_jobs re-raises HTTPStatusError on non-2xx (including 429),
        # so tenacity can intercept and retry rate-limit responses.
        async with GitHubActionsClient(settings) as client:
            return await client.get_run_jobs(repository, run_id)

    try:
        all_jobs = await _fetch_jobs()
    except Exception as exc:
        error_msg = f"Failed to fetch jobs for run {run_id} in {repository}: {exc}"
        logger.warning("github_tools.get_failed_jobs.error", error=str(exc))
        return [{"error": error_msg}]

    failed: list[dict] = []
    for job in all_jobs:
        conclusion = job.get("conclusion") or ""
        if conclusion not in ("failure", "cancelled", "timed_out"):
            continue

        steps_failed = [
            step["name"]
            for step in job.get("steps", [])
            if (step.get("conclusion") or "") in ("failure", "cancelled", "timed_out")
        ]

        failed.append(
            {
                "job_name": job.get("name", "unknown"),
                "conclusion": conclusion,
                "steps_failed": steps_failed,
            }
        )

    logger.debug(
        "github_tools.get_failed_jobs.done",
        run_id=run_id,
        repository=repository,
        failed_count=len(failed),
    )

    return failed


@tool
async def get_commit_diff(commit_sha: str, repository: str) -> str:
    """Return the unified diff introduced by a single commit.

    Use this tool when you need to understand what code changed in a specific
    commit — for example, to correlate a test failure with a recent change or
    to include the diff in a root-cause hypothesis.

    The diff is fetched from the GitHub Commits API using the
    ``application/vnd.github.diff`` media type and is truncated to 4 000
    characters when longer.

    Args:
        commit_sha: The full or abbreviated (minimum 7-character) commit SHA.
        repository: Full repository name in ``"owner/repo"`` format.

    Returns:
        Unified diff text, truncated to 4 000 chars.  Returns a descriptive
        error string on HTTP 4xx/5xx (e.g. invalid SHA, repo not found).
    """
    settings = get_settings()

    logger.debug(
        "github_tools.get_commit_diff.start",
        commit_sha=commit_sha,
        repository=repository,
    )

    url = f"https://api.github.com/repos/{repository}/commits/{commit_sha}"
    headers = {
        "Authorization": f"token {settings.github_app_id}",
        "Accept": "application/vnd.github.diff",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    @retry(**_retry_on_429())
    async def _fetch() -> httpx.Response:
        # Use a dedicated client for calls not covered by GitHubActionsClient
        # methods.  GitHubActionsClient wraps workflow-run/job endpoints; commit
        # diff uses a different Accept header so we talk to the API directly.
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            return resp

    try:
        response = await _fetch()
    except httpx.HTTPStatusError as exc:
        error_msg = (
            f"GitHub API error {exc.response.status_code} fetching diff for "
            f"commit {commit_sha} in {repository}: {exc.response.text[:200]}"
        )
        logger.warning(
            "github_tools.get_commit_diff.http_error",
            status_code=exc.response.status_code,
            commit_sha=commit_sha,
            repository=repository,
        )
        return error_msg
    except Exception as exc:
        error_msg = (
            f"Failed to fetch diff for commit {commit_sha} in {repository}: {exc}"
        )
        logger.warning("github_tools.get_commit_diff.error", error=str(exc))
        return error_msg

    diff = response.text

    if len(diff) > _TOOL_DIFF_LIMIT:
        diff = diff[:_TOOL_DIFF_LIMIT]
        diff += f"\n[... truncated to {_TOOL_DIFF_LIMIT} characters ...]"

    logger.debug(
        "github_tools.get_commit_diff.done",
        commit_sha=commit_sha,
        repository=repository,
        diff_length=len(diff),
    )

    return diff


@tool
async def get_recent_runs_for_test(
    test_name: str,
    repository: str,
    limit: int = 10,
) -> list[dict]:
    """Return recent workflow-run outcomes that match a given test or workflow name.

    Use this tool for flaky-test detection: by fetching the last N runs for a
    workflow you can compute a pass/fail rate and decide whether a failure is
    intermittent.  Results are ordered newest-first.

    The search uses the GitHub Actions ``/actions/runs`` list endpoint with a
    ``name`` query filter.  Because GitHub does not support per-test filtering
    at the API level, ``test_name`` is matched against the workflow *name* field
    (i.e. the name declared in the ``.github/workflows/*.yml`` file or the job
    name).  For finer-grained per-test statistics use the vector DB tools.

    Args:
        test_name: Workflow name or partial name to filter by (case-insensitive
            substring match applied client-side against the GitHub response).
        repository: Full repository name in ``"owner/repo"`` format.
        limit: Maximum number of run records to return (default 10, max 100).

    Returns:
        A list of dicts, one per matching run, ordered newest-first::

            [
                {
                    "run_id": 12345678,
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-05-30T14:22:01Z",
                },
                ...
            ]

        Returns an empty list when there are no matching runs.  Returns a
        single-element list containing ``{"error": "<message>"}`` on HTTP
        4xx/5xx.
    """
    settings = get_settings()

    # GitHub's per_page cap is 100.
    per_page = min(max(1, limit), 100)

    logger.debug(
        "github_tools.get_recent_runs_for_test.start",
        test_name=test_name,
        repository=repository,
        limit=limit,
    )

    url = f"https://api.github.com/repos/{repository}/actions/runs"
    headers = {
        "Authorization": f"token {settings.github_app_id}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    params = {"per_page": per_page}

    @retry(**_retry_on_429())
    async def _fetch() -> httpx.Response:
        async with httpx.AsyncClient(
            timeout=30.0, headers=headers, follow_redirects=True
        ) as http:
            resp = await http.get(url, params=params)
            resp.raise_for_status()
            return resp

    try:
        response = await _fetch()
    except httpx.HTTPStatusError as exc:
        error_msg = (
            f"GitHub API error {exc.response.status_code} fetching runs for "
            f"repository {repository}: {exc.response.text[:200]}"
        )
        logger.warning(
            "github_tools.get_recent_runs_for_test.http_error",
            status_code=exc.response.status_code,
            repository=repository,
        )
        return [{"error": error_msg}]
    except Exception as exc:
        error_msg = (
            f"Failed to fetch workflow runs for {repository}: {exc}"
        )
        logger.warning(
            "github_tools.get_recent_runs_for_test.error", error=str(exc)
        )
        return [{"error": error_msg}]

    runs = response.json().get("workflow_runs", [])

    # Client-side substring filter on the workflow/job name.
    name_lower = test_name.lower()
    matched = [
        {
            "run_id": run["id"],
            "status": run.get("status", ""),
            "conclusion": run.get("conclusion") or "",
            "created_at": run.get("created_at", ""),
        }
        for run in runs
        if name_lower in (run.get("name") or "").lower()
    ]

    # Respect the requested limit after filtering.
    matched = matched[:limit]

    logger.debug(
        "github_tools.get_recent_runs_for_test.done",
        test_name=test_name,
        repository=repository,
        matched_count=len(matched),
    )

    return matched
