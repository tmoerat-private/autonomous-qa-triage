from __future__ import annotations

import httpx
import structlog
from langchain_core.tools import tool

from src.config.settings import get_settings
from src.integrations.jenkins.client import JenkinsClient

logger = structlog.get_logger(__name__)

_TOOL_LOG_LIMIT = 8_000  # chars — tighter cap than the client-level MAX_LOG_LENGTH


@tool
async def get_build_console_log(job_name: str, build_number: int) -> str:
    """Fetch the full console output for a Jenkins build.

    Retrieves the plain-text console log produced by a Jenkins job run.
    The result is truncated to 8 000 characters if the raw log exceeds that
    limit; a truncation notice is appended so callers know the log was cut.

    Args:
        job_name: Jenkins job name, possibly including folder paths
            (e.g. ``"folder/my-job"``).
        build_number: The specific build number whose log to retrieve.

    Returns:
        Plain-text console log string, at most 8 000 characters.  On API
        error, returns a descriptive error string instead of raising.
    """
    settings = get_settings()
    logger.debug(
        "jenkins_tool.get_build_console_log.start",
        job_name=job_name,
        build_number=build_number,
    )
    try:
        async with JenkinsClient(settings) as client:
            log_text = await client.get_build_logs_for(job_name, build_number)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "jenkins_tool.get_build_console_log.http_error",
            job_name=job_name,
            build_number=build_number,
            status_code=exc.response.status_code,
        )
        return (
            f"Error fetching console log for {job_name}#{build_number}: "
            f"HTTP {exc.response.status_code} — {exc.response.text[:200]}"
        )
    except Exception as exc:
        logger.error(
            "jenkins_tool.get_build_console_log.unexpected_error",
            job_name=job_name,
            build_number=build_number,
            error=str(exc),
        )
        return f"Unexpected error fetching console log for {job_name}#{build_number}: {exc}"

    if len(log_text) > _TOOL_LOG_LIMIT:
        log_text = log_text[:_TOOL_LOG_LIMIT] + "\n[... log truncated at 8 000 characters ...]"

    logger.debug(
        "jenkins_tool.get_build_console_log.done",
        job_name=job_name,
        build_number=build_number,
        log_length=len(log_text),
    )
    return log_text


@tool
async def get_build_test_report(job_name: str, build_number: int) -> dict:
    """Fetch the structured test report for a Jenkins build.

    Queries the Jenkins JUnit test-results API (``testReport/api/json``) and
    returns a normalised summary of test outcomes.  Individual test cases are
    included with their name, status, and error message (where present).

    Args:
        job_name: Jenkins job name (may include folder paths).
        build_number: The specific build number whose test report to retrieve.

    Returns:
        A dict with the following structure::

            {
                "total":  <int>,          # total number of test cases
                "failed": <int>,          # number of failures + errors
                "cases": [
                    {
                        "name":   <str>,  # fully-qualified test case name
                        "status": <str>,  # "PASSED", "FAILED", "SKIPPED", …
                        "error":  <str>,  # error message / stack trace (may be "")
                    },
                    …
                ]
            }

        On API error (including 404 when no test report exists), returns a
        dict with an ``"error"`` key instead of raising.
    """
    settings = get_settings()
    logger.debug(
        "jenkins_tool.get_build_test_report.start",
        job_name=job_name,
        build_number=build_number,
    )

    url = (
        f"{settings.jenkins_url}/job/{job_name}/{build_number}"
        "/testReport/api/json?tree=totalCount,failCount,suites[cases[className,name,status,errorDetails]]"
    )
    auth = httpx.BasicAuth(settings.jenkins_user, settings.jenkins_token)

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(url, auth=auth)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "jenkins_tool.get_build_test_report.http_error",
            job_name=job_name,
            build_number=build_number,
            status_code=exc.response.status_code,
        )
        return {
            "error": (
                f"HTTP {exc.response.status_code} fetching test report for "
                f"{job_name}#{build_number}"
            )
        }
    except Exception as exc:
        logger.error(
            "jenkins_tool.get_build_test_report.unexpected_error",
            job_name=job_name,
            build_number=build_number,
            error=str(exc),
        )
        return {"error": f"Unexpected error: {exc}"}

    data = response.json()

    cases: list[dict] = []
    for suite in data.get("suites") or []:
        for case in suite.get("cases") or []:
            class_name = case.get("className") or ""
            test_name = case.get("name") or ""
            full_name = f"{class_name}.{test_name}" if class_name else test_name
            cases.append(
                {
                    "name": full_name,
                    "status": case.get("status") or "UNKNOWN",
                    "error": case.get("errorDetails") or "",
                }
            )

    result: dict = {
        "total": data.get("totalCount", len(cases)),
        "failed": data.get("failCount", 0),
        "cases": cases,
    }

    logger.debug(
        "jenkins_tool.get_build_test_report.done",
        job_name=job_name,
        build_number=build_number,
        total=result["total"],
        failed=result["failed"],
    )
    return result


@tool
async def get_build_parameters(job_name: str, build_number: int) -> dict:
    """Fetch the parameters used to trigger a Jenkins build.

    Extracts the ``ParametersAction`` from the build's JSON API response and
    returns the parameter name-value pairs as a flat dict.  Typical parameters
    include the source branch, commit SHA, and any custom variables set at
    trigger time.

    Args:
        job_name: Jenkins job name (may include folder paths).
        build_number: The specific build number whose parameters to retrieve.

    Returns:
        A plain ``dict`` mapping parameter names to their string values, e.g.::

            {"BRANCH": "main", "COMMIT_SHA": "abc123", "DEPLOY_ENV": "staging"}

        Returns ``{}`` if no ``ParametersAction`` is present.  On API error,
        returns a dict with an ``"error"`` key instead of raising.
    """
    settings = get_settings()
    logger.debug(
        "jenkins_tool.get_build_parameters.start",
        job_name=job_name,
        build_number=build_number,
    )

    try:
        async with JenkinsClient(settings) as client:
            build_data = await client.get_build_details_for(job_name, build_number)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "jenkins_tool.get_build_parameters.http_error",
            job_name=job_name,
            build_number=build_number,
            status_code=exc.response.status_code,
        )
        return {
            "error": (
                f"HTTP {exc.response.status_code} fetching build details for "
                f"{job_name}#{build_number}"
            )
        }
    except Exception as exc:
        logger.error(
            "jenkins_tool.get_build_parameters.unexpected_error",
            job_name=job_name,
            build_number=build_number,
            error=str(exc),
        )
        return {"error": f"Unexpected error: {exc}"}

    params: dict[str, str] = {}
    for action in build_data.get("actions") or []:
        if action.get("_class") == "hudson.model.ParametersAction":
            for param in action.get("parameters") or []:
                name = param.get("name")
                value = param.get("value")
                if name is not None:
                    params[name] = str(value) if value is not None else ""

    logger.debug(
        "jenkins_tool.get_build_parameters.done",
        job_name=job_name,
        build_number=build_number,
        param_count=len(params),
    )
    return params


@tool
async def get_recent_build_history(job_name: str, limit: int = 10) -> list[dict]:
    """Fetch recent build history for a Jenkins job.

    Returns a summary of the most recent builds including their number,
    result, and timestamp.  Useful for spotting trends such as repeated
    failures or intermittent flakiness before diving into a specific build.

    Args:
        job_name: Jenkins job name (may include folder paths).
        limit: Maximum number of recent builds to return (default 10, max 100).

    Returns:
        A list of build summary dicts ordered from most-recent to oldest::

            [
                {"number": 42, "result": "FAILURE", "timestamp": 1716825600000},
                {"number": 41, "result": "SUCCESS", "timestamp": 1716739200000},
                …
            ]

        ``result`` is one of ``"SUCCESS"``, ``"FAILURE"``, ``"ABORTED"``,
        ``"UNSTABLE"``, or ``None`` (if the build is still running).
        ``timestamp`` is milliseconds since the Unix epoch.

        On API error, returns a list containing a single error dict instead of
        raising.
    """
    settings = get_settings()
    # Guard against excessively large requests.
    safe_limit = min(max(1, limit), 100)

    logger.debug(
        "jenkins_tool.get_recent_build_history.start",
        job_name=job_name,
        limit=safe_limit,
    )

    url = (
        f"{settings.jenkins_url}/job/{job_name}/api/json"
        f"?tree=builds[number,result,timestamp]{{0,{safe_limit}}}"
    )
    auth = httpx.BasicAuth(settings.jenkins_user, settings.jenkins_token)

    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(url, auth=auth)
            response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "jenkins_tool.get_recent_build_history.http_error",
            job_name=job_name,
            status_code=exc.response.status_code,
        )
        return [
            {
                "error": (
                    f"HTTP {exc.response.status_code} fetching build history for "
                    f"{job_name}"
                )
            }
        ]
    except Exception as exc:
        logger.error(
            "jenkins_tool.get_recent_build_history.unexpected_error",
            job_name=job_name,
            error=str(exc),
        )
        return [{"error": f"Unexpected error: {exc}"}]

    builds: list[dict] = []
    for build in response.json().get("builds") or []:
        builds.append(
            {
                "number": build.get("number"),
                "result": build.get("result"),
                "timestamp": build.get("timestamp"),
            }
        )

    logger.debug(
        "jenkins_tool.get_recent_build_history.done",
        job_name=job_name,
        count=len(builds),
    )
    return builds


@tool
async def trigger_build_rerun(job_name: str, build_number: int) -> dict:
    """Trigger a rebuild of a Jenkins job.

    Submits a new build request for the given job using the Jenkins Remote
    Build API.  A CSRF crumb is fetched first where Jenkins has CSRF protection
    enabled; the tool proceeds without one when the crumb endpoint returns 403
    or 404 (i.e. CSRF is disabled on that instance).

    Jenkins responds to a successful trigger with HTTP 201 and a ``Location``
    header pointing to the queue item.  That URL is returned as
    ``new_build_url`` so callers can poll for the actual build number once it
    leaves the queue.

    Args:
        job_name: Jenkins job name (may include folder paths).
        build_number: The original failing build number — used for logging and
            included in the return value for traceability.

    Returns:
        On success::

            {
                "triggered":     True,
                "new_build_url": "<queue-item-or-build-url>",  # may be "" if
                                                                # Location header absent
            }

        On failure::

            {
                "triggered": False,
                "error":     "<description>",
            }
    """
    settings = get_settings()
    logger.debug(
        "jenkins_tool.trigger_build_rerun.start",
        job_name=job_name,
        build_number=build_number,
    )

    try:
        async with JenkinsClient(settings) as client:
            # Inline the POST so we can capture the Location header.
            # JenkinsClient.trigger_rerun does not expose the response object,
            # so we replicate its CSRF-crumb logic here and issue a single POST.
            auth = httpx.BasicAuth(settings.jenkins_user, settings.jenkins_token)
            crumb_headers: dict[str, str] = {}
            crumb_url = f"{settings.jenkins_url}/crumbIssuer/api/json"
            crumb_response = await client.client.get(crumb_url, auth=auth)
            if crumb_response.status_code == 200:
                crumb_data = crumb_response.json()
                crumb_headers[crumb_data["crumbRequestField"]] = crumb_data["crumb"]
            # 404/403 from crumb endpoint means CSRF is disabled — proceed without.

            post_url = f"{settings.jenkins_url}/job/{job_name}/build"
            post_response = await client.client.post(
                post_url, auth=auth, headers=crumb_headers
            )
            post_response.raise_for_status()
            # Jenkins 201 = queued; 200 = some instances acknowledge immediately.
            # The Location header points to the queue item URL.
            new_build_url: str = post_response.headers.get("Location", "")

    except httpx.HTTPStatusError as exc:
        logger.warning(
            "jenkins_tool.trigger_build_rerun.http_error",
            job_name=job_name,
            build_number=build_number,
            status_code=exc.response.status_code,
        )
        return {
            "triggered": False,
            "error": (
                f"HTTP {exc.response.status_code} triggering rebuild for "
                f"{job_name}#{build_number}: {exc.response.text[:200]}"
            ),
        }
    except Exception as exc:
        logger.error(
            "jenkins_tool.trigger_build_rerun.unexpected_error",
            job_name=job_name,
            build_number=build_number,
            error=str(exc),
        )
        return {"triggered": False, "error": f"Unexpected error: {exc}"}

    logger.info(
        "jenkins_tool.trigger_build_rerun.done",
        job_name=job_name,
        build_number=build_number,
        new_build_url=new_build_url,
    )
    return {"triggered": True, "new_build_url": new_build_url}
