import structlog
from fastapi import APIRouter, Request

from src.api.dependencies import DbSession
from src.observability.metrics import FAILURES_RECEIVED
from src.services.webhook_service import WebhookService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/{provider}", status_code=202)
async def receive_webhook(
    provider: str,
    request: Request,
    db: DbSession,
) -> dict:
    """Receive a CI/CD webhook event.

    Responds 202 immediately — all processing is async via Celery.
    Signature is verified before the event is accepted.
    """
    raw_body = await request.body()
    payload_dict = await request.json()

    # Check both common signature header names; providers use different ones
    signature = (
        request.headers.get("X-Hub-Signature-256")
        or request.headers.get("X-Jenkins-Signature")
    )

    service = WebhookService(db)
    result = await service.process_webhook(provider, raw_body, signature, payload_dict)
    FAILURES_RECEIVED.labels(provider=provider).inc()
    return result
