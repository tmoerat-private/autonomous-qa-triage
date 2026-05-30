import uuid

import factory
from factory import LazyFunction

from src.config.constants import CIProvider, PipelineStatus
from src.models.pipeline_event import PipelineEvent


class PipelineEventFactory(factory.Factory):
    class Meta:
        model = PipelineEvent

    id = LazyFunction(uuid.uuid4)
    provider = CIProvider.JENKINS
    provider_build_id = factory.Sequence(lambda n: f"build-{n}")
    repository = "org/my-service"
    branch = "main"
    commit_sha = factory.LazyFunction(lambda: uuid.uuid4().hex[:40])
    pipeline_name = "CI Pipeline"
    status = PipelineStatus.FAILURE
    raw_payload = factory.LazyFunction(lambda: {"build": "data", "result": "FAILURE"})
