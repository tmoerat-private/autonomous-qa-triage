"""Factory for PipelineEvent model instances.

Usage (no DB):
    event = PipelineEventFactory()

Usage (persisted):
    event = PipelineEventFactory()
    db_session.add(event)
    await db_session.flush()
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import UTC, datetime

import factory

from src.config.constants import CIProvider, PipelineStatus
from src.models.pipeline_event import PipelineEvent


def _random_commit_sha() -> str:
    """Return a realistic 40-character lowercase hex SHA-1 string."""
    return hashlib.sha1(os.urandom(20)).hexdigest()


class PipelineEventFactory(factory.Factory):
    class Meta:
        model = PipelineEvent

    id = factory.LazyFunction(uuid.uuid4)
    provider = factory.Iterator([CIProvider.GITHUB_ACTIONS, CIProvider.JENKINS])
    provider_build_id = factory.Sequence(lambda n: f"run-{n}")
    repository = "org/my-service"
    branch = "main"
    commit_sha = factory.LazyFunction(_random_commit_sha)
    pipeline_name = "CI"
    status = PipelineStatus.FAILURE
    raw_payload = factory.LazyFunction(lambda: {})
    received_at = factory.LazyFunction(lambda: datetime.now(tz=UTC))
