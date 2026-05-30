from __future__ import annotations

import uuid

import structlog

from src.agents.prompts.ticket_prompt import format_ticket_summary
from src.agents.state import TriageState
from src.config.settings import get_settings
from src.db.repositories.classification_repo import ClassificationRepository
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.ticket_repo import TicketRepository
from src.db.session import get_session_factory
from src.integrations.jira.client import JiraClient
from src.integrations.jira.mapper import build_ticket_description, map_priority

logger = structlog.get_logger(__name__)


async def ticket_creator_node(state: TriageState) -> dict:
    """Create a Jira ticket for each un-deduplicated test failure.

    For every failure in state['failure_ids']:
      1. Skip the entire node if is_duplicate=True (duplicate_detector decided
         no new ticket is needed).
      2. Load TestFailure and its FailureClassification from the DB.
      3. Build a Jira ticket summary + description using the mapper helpers.
      4. POST the issue via JiraClient and persist a TriageTicket record.

    Returns a partial state dict with:
      - ticket_id: Jira issue key (e.g. "QA-42") from the last ticket created.
      - ticket_url: Browse URL for the last ticket created.
      - errors: accumulated list of non-fatal error strings.

    Guards:
      - is_duplicate=True       → returns ticket_id=None, ticket_url=None immediately.
      - failure_ids empty        → returns ticket_id=None, ticket_url=None immediately.
      - jira_url not configured  → returns ticket_id=None, ticket_url=None immediately.
    """
    log = logger.bind(node="ticket_creator", pipeline_event_id=state["pipeline_event_id"])
    log.info("ticket_creator.started")

    # Guard 1: skip duplicates — no new ticket needed
    if state.get("is_duplicate", False):
        log.warning("ticket_creator.skipped_duplicate")
        return {"ticket_id": None, "ticket_url": None}

    # Guard 2: nothing to process
    if not state["failure_ids"]:
        return {"ticket_id": None, "ticket_url": None}

    # Guard 3: Jira not configured — degrade gracefully
    settings = get_settings()
    if not settings.jira_url:
        log.warning("ticket_creator.jira_not_configured")
        return {"ticket_id": None, "ticket_url": None}

    session_factory = get_session_factory()
    last_ticket_key: str | None = None
    last_ticket_url: str | None = None
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(session, uuid.UUID(failure_id))
                if failure is None:
                    errors.append(f"ticket_creator: TestFailure not found: {failure_id}")
                    log.warning("ticket_creator.failure_not_found", failure_id=failure_id)
                    continue

                classification = await ClassificationRepository().get_by_failure_id(
                    session, failure.id
                )
                category = classification.category if classification else "product_bug"
                confidence = classification.confidence if classification else 0.5
                reasoning = classification.reasoning if classification else None

                summary = format_ticket_summary(failure.test_name, category)
                description = build_ticket_description(
                    test_name=failure.test_name,
                    error_message=failure.error_message,
                    stack_trace=failure.stack_trace,
                    category=category,
                    confidence=confidence,
                    reasoning=reasoning,
                    repository=state.get("repository"),
                    branch=state.get("branch"),
                )
                priority = map_priority(category, confidence)

                async with JiraClient(settings) as jira:
                    ticket_data = await jira.create_issue(
                        summary=summary,
                        description=description,
                        priority=priority,
                        labels=["autonomous-qa", "test-failure", failure.test_name[:50]],
                    )

                await TicketRepository().create(
                    session,
                    test_failure_id=failure.id,
                    provider="jira",
                    external_ticket_id=ticket_data["key"],
                    external_url=ticket_data["url"],
                    title=summary,
                    description=description,
                    priority=priority,
                )
                await session.commit()

                last_ticket_key = ticket_data["key"]
                last_ticket_url = ticket_data["url"]
                log.info(
                    "ticket_creator.created",
                    failure_id=failure_id,
                    ticket_key=last_ticket_key,
                    priority=priority,
                )

        except Exception as exc:
            msg = f"ticket_creator: error for {failure_id}: {exc}"
            log.warning("ticket_creator.error", failure_id=failure_id, error=str(exc))
            errors.append(msg)

    log.info("ticket_creator.complete")
    return {"ticket_id": last_ticket_key, "ticket_url": last_ticket_url, "errors": errors}
