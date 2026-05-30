from __future__ import annotations

import structlog
from pydantic import ValidationError

from src.config.constants import CIProvider, PipelineStatus
from src.integrations.base import BaseWebhookHandler
from src.schemas.webhook_payloads import JenkinsWebhookPayload, NormalizedPipelineEvent

logger = structlog.get_logger(__name__)

# Map Jenkins build statuses to the internal PipelineStatus enum
_STATUS_MAP: dict[str, str] = {
    "SUCCESS": PipelineStatus.SUCCESS,
    "FAILURE": PipelineStatus.FAILURE,
    "ABORTED": PipelineStatus.FAILURE,
    "UNSTABLE": PipelineStatus.FAILURE,
}


class JenkinsWebhookHandler(BaseWebhookHandler):
    """Normalize Jenkins notification-plugin webhook payloads.

    Implements the ``BaseWebhookHandler`` interface so it can be swapped in
    wherever a generic webhook handler is expected.
    """

    def parse(self, raw_payload: dict) -> NormalizedPipelineEvent:
        """Validate and normalize a raw Jenkins webhook payload.

        Args:
            raw_payload: The deserialized JSON body from the Jenkins
                notification plugin.

        Returns:
            A ``NormalizedPipelineEvent`` populated from the Jenkins payload.

        Raises:
            ValueError: If the payload fails Pydantic validation.
        """
        try:
            payload = JenkinsWebhookPayload.model_validate(raw_payload)
        except ValidationError as exc:
            logger.warning(
                "jenkins.webhook_handler.parse.validation_error",
                error=str(exc),
            )
            raise ValueError(f"Invalid Jenkins payload: {exc}") from exc

        build_status = payload.build.status.upper()
        status = _STATUS_MAP.get(build_status, PipelineStatus.ERROR)

        return NormalizedPipelineEvent(
            provider=CIProvider.JENKINS,
            provider_build_id=str(payload.build.number),
            pipeline_name=payload.name,
            repository=payload.build.scm.url,
            branch=payload.build.scm.branch,
            commit_sha=payload.build.scm.commit,
            status=status,
            raw_payload=raw_payload,
        )

    @staticmethod
    def verify_signature(
        secret: str, payload_bytes: bytes, signature_header: str
    ) -> bool:
        """Verify the ``X-Jenkins-Signature`` header.

        The Jenkins notification plugin sends ``sha256=<hex>`` in the
        ``X-Jenkins-Signature`` header.  Delegates to the parent class
        ``BaseWebhookHandler.verify_signature`` which already handles the
        ``sha256=`` prefix stripping.

        Args:
            secret: The shared webhook secret configured in Jenkins.
            payload_bytes: The raw request body bytes.
            signature_header: The value of the ``X-Jenkins-Signature`` header.

        Returns:
            ``True`` if the computed digest matches the header; ``False``
            otherwise (including if the header is empty or malformed).
        """
        return BaseWebhookHandler.verify_signature(secret, payload_bytes, signature_header)
