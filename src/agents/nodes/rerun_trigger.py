from __future__ import annotations

import uuid

import structlog

from src.agents.state import TriageState
from src.config.settings import get_settings
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.repositories.rerun_repo import RerunRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


async def rerun_trigger_node(state: TriageState) -> dict:
    """Trigger an automatic CI rerun for flaky test failures.

    Skip conditions (returns rerun_triggered=False immediately):
      - state['is_flaky'] is not True: only flaky tests qualify for auto-rerun.
      - settings.enable_auto_rerun is not True: feature flag is off.

    When enabled:
      1. Load the PipelineEvent from DB to determine provider and build context.
      2. Instantiate the appropriate CI client (Jenkins or GitHub Actions).
      3. Call trigger_rerun() on the client.
      4. Persist a RerunRequest record for the first failure in failure_ids.

    Returns a partial state dict with:
      - rerun_triggered: True if the rerun was successfully dispatched.
      - rerun_job_id: The triggered job identifier, or None.
    """
    settings = get_settings()

    log = logger.bind(
        node="rerun_trigger",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info(
        "rerun_trigger.started",
        is_flaky=state.get("is_flaky"),
        enable_auto_rerun=settings.enable_auto_rerun,
    )

    # --- Skip conditions ---
    if state.get("is_flaky") is not True:
        log.warning("rerun_trigger.skipped", reason="not_flaky")
        return {"rerun_triggered": False, "rerun_job_id": None}

    if settings.enable_auto_rerun is not True:
        log.warning("rerun_trigger.skipped", reason="enable_auto_rerun_disabled")
        return {"rerun_triggered": False, "rerun_job_id": None}

    errors: list[str] = list(state["errors"])

    try:
        session_factory = get_session_factory()

        async with session_factory() as session:
            event = await PipelineEventRepository().get_by_id(
                session, uuid.UUID(state["pipeline_event_id"])
            )
            if event is None:
                log.warning(
                    "rerun_trigger.skipped",
                    reason="pipeline_event_not_found",
                    pipeline_event_id=state["pipeline_event_id"],
                )
                return {"rerun_triggered": False, "rerun_job_id": None, "errors": errors}

            provider = event.provider
            triggered_job_id: str | None = None

            if provider == "jenkins":
                from src.integrations.jenkins.client import JenkinsClient

                async with JenkinsClient(settings) as client:
                    result = await client.trigger_rerun(
                        job_name=event.pipeline_name or "unknown",
                        build_number=int(event.provider_build_id or "0"),
                    )
                triggered_job_id = result.get("job_name")

            elif provider == "github_actions":
                from src.integrations.github_actions.client import GitHubActionsClient

                async with GitHubActionsClient(settings) as client:
                    result = await client.trigger_rerun(
                        repo_full_name=event.repository or "",
                        run_id=int(event.provider_build_id or "0"),
                    )
                triggered_job_id = str(result.get("run_id"))

            else:
                log.warning(
                    "rerun_trigger.skipped",
                    reason="unsupported_provider",
                    provider=provider,
                )
                return {"rerun_triggered": False, "rerun_job_id": None, "errors": errors}

            log.info(
                "rerun_trigger.triggered",
                provider=provider,
                triggered_job_id=triggered_job_id,
            )

            # Persist a RerunRequest for the first failure if failure_ids is populated.
            if state["failure_ids"]:
                await RerunRepository().create(
                    session,
                    test_failure_id=uuid.UUID(state["failure_ids"][0]),
                    provider=provider,
                    triggered_job_id=triggered_job_id,
                    trigger_reason="flaky_detected",
                    status="triggered",
                )
                await session.commit()

    except Exception as exc:
        msg = f"rerun_trigger: error: {exc}"
        log.warning("rerun_trigger.error", error=str(exc))
        errors.append(msg)
        return {"rerun_triggered": False, "rerun_job_id": None, "errors": errors}

    return {
        "rerun_triggered": True,
        "rerun_job_id": triggered_job_id,
        "errors": errors,
    }
