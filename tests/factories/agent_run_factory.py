"""Factory for AgentRun model instances.

AgentRun.input_summary / output_summary are Text columns (not JSONB).
AgentRun.error_message does not exist on the model — the column is absent; do not
set it here.

Usage (persisted — test_failure must already be flushed):
    run = AgentRunFactory(test_failure_id=failure.id)
    db_session.add(run)
    await db_session.flush()
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import factory

from src.config.constants import AgentRunStatus
from src.models.agent_run import AgentRun


class AgentRunFactory(factory.Factory):
    class Meta:
        model = AgentRun

    id = factory.LazyFunction(uuid.uuid4)
    # test_failure_id must be supplied by the caller when persisting to the DB.
    test_failure_id = factory.LazyFunction(uuid.uuid4)
    agent_name = factory.Iterator(
        [
            "failure_classifier",
            "log_analyzer",
            "duplicate_detector",
            "ticket_creator",
            "notifier",
            "heal_suggester",
        ]
    )
    status = AgentRunStatus.COMPLETED
    input_summary = "Received failure event with 1 test failure."
    output_summary = "Classification complete: product_bug (confidence=0.92)."
    started_at = factory.LazyFunction(lambda: datetime.now(tz=timezone.utc))
    completed_at = factory.LazyFunction(lambda: datetime.now(tz=timezone.utc))
    tokens_used = factory.Faker("random_int", min=200, max=3000)
    duration_ms = factory.Faker("random_int", min=500, max=15000)
