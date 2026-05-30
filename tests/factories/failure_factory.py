import uuid

import factory
from factory import LazyFunction, SubFactory

from src.config.constants import FailureStatus
from src.models.test_failure import TestFailure
from tests.factories.pipeline_factory import PipelineEventFactory


class TestFailureFactory(factory.Factory):
    class Meta:
        model = TestFailure

    id = LazyFunction(uuid.uuid4)
    pipeline_event = SubFactory(PipelineEventFactory)
    pipeline_event_id = factory.SelfAttribute("pipeline_event.id")
    test_name = factory.Sequence(lambda n: f"test_user_login_{n}")
    test_suite = "AuthenticationTests"
    test_file = "tests/auth/test_login.py"
    error_message = "AssertionError: Expected 200, got 500"
    stack_trace = (
        "Traceback (most recent call last):\n"
        "  File 'test_login.py', line 42\n"
        "AssertionError: Expected 200, got 500"
    )
    duration_ms = 1234
    retry_count = 0
    status = FailureStatus.NEW
