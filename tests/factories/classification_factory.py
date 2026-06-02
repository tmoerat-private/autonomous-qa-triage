"""Factory for FailureClassification model instances.

FailureClassification has a UNIQUE constraint on test_failure_id (one-to-one).
Always pass a distinct failure instance when creating multiple classifications.

Usage (persisted):
    event = PipelineEventFactory()
    db_session.add(event)
    await db_session.flush()

    failure = TestFailureFactory(pipeline_event=event, pipeline_event_id=event.id)
    db_session.add(failure)
    await db_session.flush()

    classification = FailureClassificationFactory(test_failure_id=failure.id)
    db_session.add(classification)
    await db_session.flush()
"""
from __future__ import annotations

import random
import uuid

import factory

from src.config.constants import DEFAULT_MODEL, FailureCategory
from src.models.failure_classification import FailureClassification


def _random_confidence() -> float:
    return round(random.uniform(0.70, 0.99), 2)


class FailureClassificationFactory(factory.Factory):
    class Meta:
        model = FailureClassification

    id = factory.LazyFunction(uuid.uuid4)
    # test_failure_id must be supplied by the caller (FK to an already-flushed
    # TestFailure row).  The placeholder below keeps build() happy in unit tests
    # that never touch the DB.
    test_failure_id = factory.LazyFunction(uuid.uuid4)
    category = factory.Iterator(
        [
            FailureCategory.PRODUCT_BUG,
            FailureCategory.FLAKY_TEST,
            FailureCategory.ENV_ISSUE,
            FailureCategory.TIMEOUT,
            FailureCategory.INFRA_ISSUE,
        ]
    )
    confidence = factory.LazyFunction(_random_confidence)
    reasoning = "Assertion failed in business logic — return value did not match expected contract."
    model_used = DEFAULT_MODEL
    tokens_used = factory.Faker("random_int", min=500, max=2000)
