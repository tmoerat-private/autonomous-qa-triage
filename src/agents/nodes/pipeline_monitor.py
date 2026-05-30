from __future__ import annotations

import uuid

import structlog

from src.agents.state import TriageState
from src.config.constants import CIProvider
from src.config.settings import get_settings
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.session import get_session_factory
from src.integrations.github_actions.client import GitHubActionsClient
from src.integrations.github_actions.parser import GitHubActionsParser
from src.integrations.jenkins.client import JenkinsClient
from src.integrations.jenkins.parser import JenkinsParser
from src.schemas.webhook_payloads import GitHubActionsWebhookPayload, JenkinsWebhookPayload

logger = structlog.get_logger(__name__)


async def pipeline_monitor_node(state: TriageState) -> dict:
    """Pipeline Monitor agent node.

    Responsibilities:
    1. Load PipelineEvent from DB
    2. Fetch build logs from CI provider
    3. Parse failures from logs
    4. Save TestFailure records to DB
    5. Update PipelineEvent status to "triaging"

    Returns a partial state dict with updated fields.
    """
    settings = get_settings()
    session_factory = get_session_factory()
    pipeline_event_id = state["pipeline_event_id"]
    log = logger.bind(pipeline_event_id=pipeline_event_id, node="pipeline_monitor")

    # ------------------------------------------------------------------
    # Step 1 — Load PipelineEvent (read-only session, no commit needed)
    # ------------------------------------------------------------------

    async with session_factory() as session:
        pipeline_repo = PipelineEventRepository()
        event = await pipeline_repo.get_by_id(session, uuid.UUID(pipeline_event_id))
        if event is None:
            log.error("pipeline_monitor.event_not_found")
            return {
                "errors": state["errors"] + [f"PipelineEvent not found: {pipeline_event_id}"]
            }

        provider = event.provider
        raw_payload = event.raw_payload
        pipeline_name = event.pipeline_name
        repository = event.repository
        branch = event.branch
        log = log.bind(provider=provider)

    # ------------------------------------------------------------------
    # Step 2 — Fetch build logs from the CI provider
    # ------------------------------------------------------------------

    raw_logs: str

    if provider == CIProvider.JENKINS:
        try:
            payload = JenkinsWebhookPayload.model_validate(raw_payload)
            job_name, build_number = JenkinsParser().extract_job_info(payload)
            async with JenkinsClient(settings) as client:
                raw_logs = await client.get_build_logs_for(job_name, build_number)
        except Exception as exc:
            log.warning("pipeline_monitor.jenkins_logs_failed", error=str(exc))
            raw_logs = ""

    elif provider == CIProvider.GITHUB_ACTIONS:
        try:
            gh_payload = GitHubActionsWebhookPayload.model_validate(raw_payload)
            repo_full_name, run_id = GitHubActionsParser().extract_run_info(gh_payload)
            async with GitHubActionsClient(settings) as client:
                raw_logs = await client.get_build_logs(repo_full_name, run_id)
        except Exception as exc:
            log.warning("pipeline_monitor.github_logs_failed", error=str(exc))
            raw_logs = ""

    else:
        log.warning("pipeline_monitor.unsupported_provider")
        raw_logs = ""

    # ------------------------------------------------------------------
    # Step 3 — Parse failures from the fetched logs
    # ------------------------------------------------------------------

    if provider == CIProvider.JENKINS:
        parsed_failures = JenkinsParser().parse_failures(raw_logs)
    elif provider == CIProvider.GITHUB_ACTIONS:
        parsed_failures = GitHubActionsParser().parse_failures(raw_logs)
    else:
        parsed_failures = []

    log.info("pipeline_monitor.failures_parsed", count=len(parsed_failures))

    # ------------------------------------------------------------------
    # Step 4 — Save TestFailure records and update PipelineEvent status
    #          (separate write session; commits at the end of the block)
    # ------------------------------------------------------------------

    async with session_factory() as session:
        failure_repo = FailureRepository()
        pipeline_repo = PipelineEventRepository()

        failure_dicts = [f.model_dump() for f in parsed_failures]
        saved_failures = await failure_repo.create_many(
            session,
            pipeline_event_id=uuid.UUID(pipeline_event_id),
            failures=failure_dicts,
        )
        await pipeline_repo.update_status(
            session, uuid.UUID(pipeline_event_id), "triaging"
        )
        await session.commit()

    log.info(
        "pipeline_monitor.complete",
        failures_saved=len(saved_failures),
        has_logs=bool(raw_logs),
    )

    # ------------------------------------------------------------------
    # Step 5 — Return updated state fields
    # ------------------------------------------------------------------

    return {
        "provider": provider,
        "pipeline_name": pipeline_name,
        "repository": repository,
        "branch": branch,
        "raw_logs": raw_logs,
        "parsed_failures": [f.model_dump() for f in parsed_failures],
        "failure_ids": [str(tf.id) for tf in saved_failures],
    }
