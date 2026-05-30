import structlog
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.constants import CIProvider
from src.config.settings import get_settings
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.integrations.base import BaseWebhookHandler
from src.integrations.github_actions.webhook_handler import GitHubActionsWebhookHandler
from src.integrations.jenkins.webhook_handler import JenkinsWebhookHandler
from src.workers.tasks import run_triage_pipeline

logger = structlog.get_logger(__name__)

_HANDLERS: dict[str, BaseWebhookHandler] = {
    CIProvider.JENKINS: JenkinsWebhookHandler(),
    CIProvider.GITHUB_ACTIONS: GitHubActionsWebhookHandler(),
}


def _get_secret(provider: str) -> str:
    settings = get_settings()
    return {
        CIProvider.JENKINS: settings.jenkins_webhook_secret,
        CIProvider.GITHUB_ACTIONS: settings.github_webhook_secret,
    }.get(provider, "")


class WebhookService:
    def __init__(self, db_session: AsyncSession) -> None:
        self.db_session = db_session
        self.pipeline_repo = PipelineEventRepository()

    async def process_webhook(
        self,
        provider: str,
        raw_body: bytes,
        signature_header: str | None,
        payload_dict: dict,
    ) -> dict:
        log = logger.bind(provider=provider)

        # 1. Look up handler
        handler = _HANDLERS.get(provider)
        if handler is None:
            log.warning("webhook.unsupported_provider")
            raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")

        # 2. Verify HMAC signature (skip if secret is not configured — dev convenience)
        secret = _get_secret(provider)
        if secret:
            sig = signature_header or ""
            if not handler.verify_signature(secret, raw_body, sig):
                log.warning("webhook.invalid_signature")
                raise HTTPException(status_code=401, detail="Invalid webhook signature")

        # 3. Parse the payload
        try:
            normalized = handler.parse(payload_dict)
        except ValueError as exc:
            log.warning("webhook.parse_error", error=str(exc))
            raise HTTPException(status_code=422, detail=str(exc))

        # 4. Persist to DB
        pipeline_event = await self.pipeline_repo.create(
            session=self.db_session,
            provider=normalized.provider,
            provider_build_id=normalized.provider_build_id,
            repository=normalized.repository,
            branch=normalized.branch,
            commit_sha=normalized.commit_sha,
            pipeline_name=normalized.pipeline_name,
            status=normalized.status,
            raw_payload=normalized.raw_payload,
        )

        # 5. Enqueue Celery task
        run_triage_pipeline.delay(str(pipeline_event.id))
        log.info("webhook.accepted", pipeline_event_id=str(pipeline_event.id))

        return {"status": "accepted", "pipeline_event_id": str(pipeline_event.id)}
