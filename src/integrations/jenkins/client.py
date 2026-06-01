from __future__ import annotations

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

class JenkinsClient(BaseCIClient):
    """Async Jenkins REST API client.

    Jenkins builds are identified by a *job name* plus a *build number*, not
    a single opaque string.  The base-class ``get_build_details(build_id)``
    and ``get_build_logs(build_id)`` abstract methods are therefore satisfied
    by delegating to the concrete typed methods below via a ``"job::number"``
    convention; callers should prefer the typed methods directly.

    Must be used as an async context manager so the underlying
    ``httpx.AsyncClient`` is properly initialised and torn down::

        async with JenkinsClient(settings) as client:
            details = await client.get_build_details_for("my-job", 42)
            logs = await client.get_build_logs_for("my-job", 42)
    """

    def __init__(self, settings) -> None:
        super().__init__(settings)

    # ------------------------------------------------------------------
    # Abstract base-class methods
    # Jenkins requires job_name + build_number, not a single build_id.
    # These satisfy the ABC contract; callers should use the typed methods.
    # ------------------------------------------------------------------

    async def get_build_details(self, build_id: str) -> dict:
        """Satisfy ``BaseCIClient`` ABC.  Raises ``NotImplementedError``.

        Use ``get_build_details_for(job_name, build_number)`` instead.
        """
        raise NotImplementedError("Use job_name + build_number overloads")

    async def get_build_logs(self, build_id: str) -> str:
        """Satisfy ``BaseCIClient`` ABC.  Raises ``NotImplementedError``.

        Use ``get_build_logs_for(job_name, build_number)`` instead.
        """
        raise NotImplementedError("Use job_name + build_number overloads")

    # ------------------------------------------------------------------
    # Concrete typed methods — the real public API
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_build_details_for(
        self, job_name: str, build_number: int
    ) -> dict:
        """Fetch build metadata from the Jenkins JSON API.

        Args:
            job_name: The Jenkins job name (may include folder paths, e.g.
                ``"folder/my-job"``).
            build_number: The specific build number to retrieve.

        Returns:
            Parsed JSON response as a plain ``dict``.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        url = f"{self.settings.jenkins_url}/job/{job_name}/{build_number}/api/json"
        auth = httpx.BasicAuth(self.settings.jenkins_user, self.settings.jenkins_token)

        logger.debug(
            "jenkins.get_build_details.request",
            job_name=job_name,
            build_number=build_number,
            url=url,
        )

        try:
            response = await self.client.get(url, auth=auth)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "jenkins.get_build_details.http_error",
                job_name=job_name,
                build_number=build_number,
                status_code=exc.response.status_code,
            )
            raise

        return response.json()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
    )
    async def get_build_logs_for(
        self, job_name: str, build_number: int
    ) -> str:
        """Fetch the plain-text console log for a Jenkins build.

        The returned text is truncated to ``MAX_LOG_LENGTH`` characters if
        the raw log exceeds that limit; a WARNING-level structlog event is
        emitted when truncation occurs.

        Args:
            job_name: The Jenkins job name.
            build_number: The specific build number.

        Returns:
            Plain-text console log, possibly truncated to ``MAX_LOG_LENGTH``
            characters.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses (after retries).
        """
        url = f"{self.settings.jenkins_url}/job/{job_name}/{build_number}/consoleText"
        auth = httpx.BasicAuth(self.settings.jenkins_user, self.settings.jenkins_token)

        logger.debug(
            "jenkins.get_build_logs.request",
            job_name=job_name,
            build_number=build_number,
            url=url,
        )

        try:
            response = await self.client.get(url, auth=auth)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "jenkins.get_build_logs.http_error",
                job_name=job_name,
                build_number=build_number,
                status_code=exc.response.status_code,
            )
            raise

        text = response.text

        if len(text) > MAX_LOG_LENGTH:
            logger.warning(
                "jenkins.get_build_logs.truncated",
                job_name=job_name,
                build_number=build_number,
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
    async def trigger_rerun(self, job_name: str, build_number: int) -> dict:
        """Trigger a new build of the same Jenkins job via the Remote Build API.

        Attempts to fetch a CSRF crumb first (POST /crumbIssuer/api/json).
        If the crumb endpoint returns 404/403, proceeds without it (some Jenkins
        instances have CSRF protection disabled).

        Args:
            job_name: The Jenkins job name (may include folder paths).
            build_number: The original failing build number (used only for logging).

        Returns:
            {"triggered": True, "job_name": job_name, "build_number": build_number}

        Raises:
            httpx.HTTPStatusError: If the build trigger POST returns a non-2xx
                status after retries.
        """
        auth = httpx.BasicAuth(self.settings.jenkins_user, self.settings.jenkins_token)

        # Attempt to fetch a CSRF crumb; skip gracefully if CSRF is disabled.
        crumb_headers: dict[str, str] = {}
        crumb_url = f"{self.settings.jenkins_url}/crumbIssuer/api/json"
        crumb_response = await self.client.get(crumb_url, auth=auth)
        if crumb_response.status_code == 200:
            crumb_data = crumb_response.json()
            crumb_headers[crumb_data["crumbRequestField"]] = crumb_data["crumb"]
        # 404 or 403 means CSRF is disabled — proceed without crumb headers.

        build_url = f"{self.settings.jenkins_url}/job/{job_name}/build"

        logger.debug(
            "jenkins.trigger_rerun.request",
            job_name=job_name,
            build_number=build_number,
            url=build_url,
            has_crumb=bool(crumb_headers),
        )

        response = await self.client.post(build_url, auth=auth, headers=crumb_headers)
        response.raise_for_status()

        logger.info(
            "jenkins.trigger_rerun.triggered",
            job_name=job_name,
            build_number=build_number,
            status_code=response.status_code,
        )

        return {"triggered": True, "job_name": job_name, "build_number": build_number}
