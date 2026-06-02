"""Factory for TestFailure model instances.

Usage (no DB):
    failure = TestFailureFactory()

Usage (persisted — pipeline event must already be flushed):
    event = PipelineEventFactory()
    db_session.add(event)
    await db_session.flush()

    failure = TestFailureFactory(
        pipeline_event=event,
        pipeline_event_id=event.id,
    )
    db_session.add(failure)
    await db_session.flush()
"""
from __future__ import annotations

import uuid

import factory

from src.config.constants import FailureStatus
from src.models.test_failure import TestFailure
from tests.factories.pipeline_factory import PipelineEventFactory

_REALISTIC_STACK_TRACE = """\
Traceback (most recent call last):
  File "tests/unit/services/test_checkout.py", line 58, in test_checkout_total
    assert result.total == expected_total, f"Got {result.total}, expected {expected_total}"
AssertionError: Got 0.00, expected 99.99"""


class TestFailureFactory(factory.Factory):
    class Meta:
        model = TestFailure

    id = factory.LazyFunction(uuid.uuid4)
    # Callers that persist to the DB must pass pipeline_event_id explicitly
    # (or pass both pipeline_event= and pipeline_event_id=) to satisfy the FK.
    pipeline_event = factory.SubFactory(PipelineEventFactory)
    pipeline_event_id = factory.SelfAttribute("pipeline_event.id")
    test_name = factory.Sequence(lambda n: f"test_feature_{n}")
    test_suite = "tests.unit.services.test_checkout"
    test_file = "tests/unit/services/test_checkout.py"
    error_message = "AssertionError: Got 0.00, expected 99.99"
    stack_trace = _REALISTIC_STACK_TRACE
    duration_ms = factory.Faker("random_int", min=100, max=30000)
    retry_count = 0
    status = FailureStatus.NEW
