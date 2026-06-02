"""Factory for TriageTicket model instances.

TriageTicket has a UNIQUE constraint on test_failure_id (one-to-one).
Always pass a distinct failure instance when creating multiple tickets.

Usage (persisted — test_failure must already be flushed):
    ticket = TriageTicketFactory(test_failure_id=failure.id)
    db_session.add(ticket)
    await db_session.flush()
"""
from __future__ import annotations

import uuid

import factory

from src.config.constants import TicketPriority, TicketProvider
from src.models.triage_ticket import TriageTicket


class TriageTicketFactory(factory.Factory):
    class Meta:
        model = TriageTicket

    id = factory.LazyFunction(uuid.uuid4)
    # test_failure_id must be supplied by the caller when persisting to the DB.
    test_failure_id = factory.LazyFunction(uuid.uuid4)
    provider = TicketProvider.JIRA
    external_ticket_id = factory.Sequence(lambda n: f"PROJ-{100 + n}")
    external_url = factory.LazyAttribute(
        lambda obj: f"https://jira.example.com/browse/{obj.external_ticket_id}"
    )
    title = factory.Sequence(lambda n: f"[Auto] Test failure: test_feature_{n}")
    description = (
        "Automatically created by the Autonomous QA triage agent.\n\n"
        "**Error:** AssertionError: Got 0.00, expected 99.99\n\n"
        "See the attached stack trace for details."
    )
    priority = TicketPriority.HIGH
    assignee = None
    status = "open"
