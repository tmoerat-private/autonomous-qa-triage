from __future__ import annotations

import hashlib
import hmac
from abc import ABC, abstractmethod

import httpx

from src.schemas.webhook_payloads import NormalizedPipelineEvent


class BaseWebhookHandler(ABC):
    """Abstract base for provider-specific webhook handlers.

    Each CI/CD provider subclass implements ``parse()`` to normalize its
    webhook payload into the common ``NormalizedPipelineEvent`` schema.
    The concrete ``verify_signature()`` static method handles HMAC-SHA256
    verification for all providers (callers pass the appropriate header value).
    """

    @staticmethod
    def verify_signature(
        secret: str, payload_bytes: bytes, signature_header: str
    ) -> bool:
        """Verify an HMAC-SHA256 webhook signature.

        Accepts signatures in raw hex form or with a ``sha256=`` prefix
        (as used by GitHub and Jenkins).  Returns ``False`` if the header is
        empty, malformed, or the digest does not match.
        """
        if not signature_header:
            return False

        # Strip "sha256=" prefix if present
        sig_value = signature_header
        if sig_value.startswith("sha256="):
            sig_value = sig_value[len("sha256="):]

        expected = hmac.new(
            secret.encode("utf-8"), payload_bytes, hashlib.sha256
        ).hexdigest()

        try:
            return hmac.compare_digest(expected, sig_value)
        except (TypeError, ValueError):
            return False

    @abstractmethod
    def parse(self, raw_payload: dict) -> NormalizedPipelineEvent:
        """Parse a provider-specific webhook payload into the normalized form.

        Raises:
            ValueError: If the payload is invalid or cannot be parsed.
        """


class BaseCIClient(ABC):
    """Abstract base for provider-specific CI/CD API clients.

    Subclasses wrap an ``httpx.AsyncClient`` and are intended to be used as
    async context managers::

        async with JenkinsClient(settings) as client:
            logs = await client.get_build_logs("my-job", 42)
    """

    def __init__(self, settings) -> None:  # noqa: ANN001
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> BaseCIClient:
        self._client = httpx.AsyncClient(timeout=30.0)
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:  # noqa: ANN001
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError(
                f"{self.__class__.__name__} must be used as an async context manager"
            )
        return self._client

    @abstractmethod
    async def get_build_details(self, build_id: str) -> dict:
        """Fetch build metadata from the CI provider API.

        Args:
            build_id: Provider-specific build identifier.

        Returns:
            Raw API response as a dict.

        Raises:
            httpx.HTTPStatusError: On non-2xx responses.
        """

    @abstractmethod
    async def get_build_logs(self, build_id: str) -> str:
        """Fetch raw console/log text for a build.

        Args:
            build_id: Provider-specific build identifier.

        Returns:
            Plain-text log content, possibly truncated to MAX_LOG_LENGTH.
        """
