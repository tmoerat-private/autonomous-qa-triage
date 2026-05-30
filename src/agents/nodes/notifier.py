from __future__ import annotations

import uuid
from datetime import UTC, datetime

import structlog

from src.agents.state import TriageState
from src.config.settings import get_settings
from src.db.repositories.classification_repo import ClassificationRepository
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.notification_repo import NotificationRepository
from src.db.session import get_session_factory
from src.integrations.slack.client import SlackClient
from src.integrations.slack.message_builder import build_triage_notification

logger = structlog.get_logger(__name__)


async def notifier_node(state: TriageState) -> dict:
    """Send a Slack notification for every failure processed in this triage run.

    For every failure in state['failure_ids']:
      1. Load TestFailure and its FailureClassification from the DB.
      2. Build a Slack Block Kit payload via build_triage_notification(), passing
         ticket details (if a ticket was created) and the is_duplicate flag so the
         message header differs for duplicate vs. new failures.
      3. POST the message via SlackClient and persist a Notification record.

    Returns a partial state dict with:
      - notification_sent: True if at least one Slack message was delivered.
      - errors: accumulated list of non-fatal error strings.

    Guards:
      - slack_bot_token not configured → returns notification_sent=False immediately.
      - failure_ids empty              → returns notification_sent=False immediately.
    """
    log = logger.bind(node="notifier", pipeline_event_id=state["pipeline_event_id"])
    log.info("notifier.started")

    settings = get_settings()

    # Guard 1: Slack not configured — degrade gracefully
    if not settings.slack_bot_token:
        log.warning("notifier.slack_not_configured")
        return {"notification_sent": False}

    # Guard 2: nothing to process
    if not state["failure_ids"]:
        return {"notification_sent": False}

    channel_id = settings.slack_channel_id
    session_factory = get_session_factory()
    notification_sent = False
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(session, uuid.UUID(failure_id))
                if failure is None:
                    errors.append(f"notifier: TestFailure not found: {failure_id}")
                    log.warning("notifier.failure_not_found", failure_id=failure_id)
                    continue

                classification = await ClassificationRepository().get_by_failure_id(
                    session, failure.id
                )
                category = classification.category if classification else "unknown"
                confidence = classification.confidence if classification else 0.0
                reasoning = classification.reasoning if classification else None

                message_payload = build_triage_notification(
                    test_name=failure.test_name,
                    category=category,
                    confidence=confidence,
                    reasoning=reasoning,
                    repository=state.get("repository"),
                    branch=state.get("branch"),
                    ticket_url=state.get("ticket_url"),
                    ticket_key=state.get("ticket_id"),
                    is_duplicate=state.get("is_duplicate", False),
                )

                slack = SlackClient(settings)
                response = await slack.post_message(
                    channel_id=channel_id,
                    blocks=message_payload["blocks"],
                    text=message_payload["text"],
                )

                external_message_id = response.get("ts")
                await NotificationRepository().create(
                    session,
                    test_failure_id=failure.id,
                    channel="slack",
                    recipient=channel_id,
                    message_type="triage_result",
                    external_message_id=external_message_id,
                    sent_at=datetime.now(UTC),
                )
                await session.commit()

                notification_sent = True
                log.info("notifier.sent", failure_id=failure_id, ts=external_message_id)

        except Exception as exc:
            msg = f"notifier: error for {failure_id}: {exc}"
            log.warning("notifier.error", failure_id=failure_id, error=str(exc))
            errors.append(msg)

    log.info("notifier.complete", notification_sent=notification_sent)
    return {"notification_sent": notification_sent, "errors": errors}
