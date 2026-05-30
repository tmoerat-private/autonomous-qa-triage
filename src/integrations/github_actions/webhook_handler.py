from __future__ import annotations

import structlog
from pydantic import ValidationError

from src.config.constants import CIProvider, PipelineStatus
from src.integrations.base import BaseWebhookHandler
from src.schemas.webhook_payloads import (
    GitHubActionsWebhookPayload,
    NormalizedPipelineEvent,
)

logger = structlog.get_logger(__name__)

# Map GitHub Actions conclusion strings to our internal PipelineStatus values.
_CONCLUSION_TO_STATUS: dict[str, PipelineStatus] = {
    "failure": PipelineStatus.FAILURE,
    "timed_out": PipelineStatus.FAILURE,
    "startup_failure": PipelineStatus.FAILURE,
    "success": PipelineStatus.SUCCESS,
    "cancelled": PipelineStatus.ERROR,
    "skipped": PipelineStatus.ERROR,
    "neutral": PipelineStatus.ERROR,
    "action_required": PipelineStatus.ERROR,
    "stale": PipelineStatus.ERROR,
}


class GitHubActionsWebhookHandler(BaseWebhookHandler):
    """Normalize GitHub Actions ``workflow_run`` webhook payloads.

    Validates the incoming raw payload against ``GitHubActionsWebhookPayload``,
    then maps provider-specific fields to the common ``NormalizedPipelineEvent``
    schema.  Only ``"completed"`` actions are processed; all others cause a
    ``ValueError`` so the caller can return an early ``202 Accepted`` without
    dispatching a triage job.

    Signature verification delegates to ``BaseWebhookHandler.verify_signature``
    which already handles GitHub's ``sha256=<hex>`` header format.
    """

    def parse(self, raw_payload: dict) -> NormalizedPipelineEvent:
        """Parse and normalize a GitHub Actions webhook payload.

        Args:
            raw_payload: The decoded JSON body received from GitHub.

        Returns:
            A ``NormalizedPipelineEvent`` populated from the workflow-run data.

        Raises:
            ValueError: If the payload fails Pydantic validation, or if the
                ``action`` field is not ``"completed"``.
        """
        try:
            payload = GitHubActionsWebhookPayload.model_validate(raw_payload)
        except ValidationError as exc:
            logger.warning(
                "github_actions.webhook_handler.parse.validation_error",
                error=str(exc),
            )
            raise ValueError(f"Invalid GitHub Actions payload: {exc}") from exc

        if payload.action != "completed":
            logger.warning(
                "github_actions.webhook_handler.parse.non_completed_action",
                action=payload.action,
            )
            raise ValueError(
                f"Ignoring non-completed GitHub Actions event: action={payload.action}"
            )

        run = payload.workflow_run
        conclusion = run.conclusion or ""
        status = _CONCLUSION_TO_STATUS.get(conclusion, PipelineStatus.ERROR)

        return NormalizedPipelineEvent(
            provider=CIProvider.GITHUB_ACTIONS,
            provider_build_id=str(run.id),
            repository=run.repository.full_name,
            branch=run.head_branch,
            commit_sha=run.head_sha,
            pipeline_name=run.name,
            status=status,
            raw_payload=raw_payload,
        )

    @staticmethod
    def verify_signature(
        secret: str, payload_bytes: bytes, signature_header: str
    ) -> bool:
        """Verify the ``X-Hub-Signature-256`` header sent by GitHub.

        GitHub uses the format ``sha256=<hex-digest>``.  This method delegates
        to ``BaseWebhookHandler.verify_signature`` which strips the prefix
        automatically before comparing digests.

        Args:
            secret: The raw webhook secret configured in the GitHub repo/org.
            payload_bytes: The raw request body bytes.
            signature_header: The value of the ``X-Hub-Signature-256`` header.

        Returns:
            ``True`` if the digest matches, ``False`` otherwise.
        """
        return BaseWebhookHandler.verify_signature(
            secret, payload_bytes, signature_header
        )
