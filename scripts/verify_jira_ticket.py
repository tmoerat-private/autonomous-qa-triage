"""Standalone script to verify ticket_creator_node against the REAL Jira API.

Background
-----------
Commit 5afeff6 fixed a bug where ticket_creator_node sent Jira labels
containing spaces (e.g. a Playwright test named "dark mode toggle is visible
and clickable") and got a 400 Bad Request from the Jira API. The fix added
``slugify_label()`` in ``src/integrations/jira/mapper.py`` and applies it in
``src/agents/nodes/ticket_creator.py``::

    labels=["autonomous-qa", "test-failure", slugify_label(failure.test_name)]

This is covered by 10 passing unit tests, but has never been exercised
against the real Jira API end-to-end — every previous attempt hit
``duplicate_detector_node`` (which routes to ``notifier`` and skips
``ticket_creator`` entirely whenever ``is_duplicate=True``).

What this script does
----------------------
1. Inserts a synthetic ``PipelineEvent`` (provider="github_actions").
2. Inserts a synthetic ``TestFailure`` under that event with:
     - a test_name containing SPACES, to specifically exercise slugify_label
     - an error_message/stack_trace containing a brand-new uuid4 token, so
       ``normalize_error()`` produces a signature that has never been seen
       before -- guaranteeing duplicate_detector finds no match.
3. Runs the relevant *tail* of the triage graph for just this one failure:
   failure_classifier -> log_analyzer -> root_cause -> duplicate_detector
   -> ticket_creator.
4. Prints the resulting TriageTicket (or the ticket_creator AgentRun error).

Why not ``run_triage(pipeline_event_id)`` / ``triage_graph.ainvoke()``?
-------------------------------------------------------------------------
The full graph's entry point is ``pipeline_monitor_node``, which:
  - loads ``raw_payload`` from the PipelineEvent,
  - tries to call the real CI provider's API (GitHubActionsClient /
    JenkinsClient) to fetch build logs based on that payload,
  - re-parses failures from those logs and OVERWRITES
    ``parsed_failures`` / ``failure_ids`` with whatever (likely nothing) it
    finds.

A synthetic raw_payload has no real CI build behind it, so
pipeline_monitor_node would either raise/hang trying to reach a real CI API,
or (after its try/except) silently produce ``raw_logs=""`` and
``parsed_failures=[]`` -- wiping out the synthetic failure_id we just
inserted before failure_classifier ever sees it.

The smallest correct workaround is to **skip pipeline_monitor_node entirely**
and invoke the downstream nodes directly with a hand-built TriageState whose
``failure_ids`` already points at our synthetic TestFailure. Each downstream
node (failure_classifier, log_analyzer, root_cause, duplicate_detector,
ticket_creator) loads the TestFailure from the DB by id and only reads
``state["failure_ids"]`` / ``state["repository"]`` / ``state["branch"]`` /
per-failure result dicts populated by earlier nodes in this same chain -- it
does not depend on pipeline_monitor having run in the same process.

Usage
-----
    uv run python scripts/verify_jira_ticket.py --dry-run
    uv run python scripts/verify_jira_ticket.py

The ``--dry-run`` flag performs steps 1-2 only (creates the synthetic
PipelineEvent + TestFailure, prints their IDs) and exits BEFORE invoking any
graph node, LLM, or the Jira API.

Without ``--dry-run`` this script:
  - calls the real Anthropic API (failure_classifier, log_analyzer's
    sub-calls if any, root_cause) -- consumes API credits, and
  - calls the real Jira API (ticket_creator) -- creates a real ticket in
    whatever project ``settings.jira_*`` points at.

Requirements:
    - PostgreSQL accessible at DATABASE_URL (docker compose up)
    - ANTHROPIC_API_KEY and JIRA_* settings configured (non-dry-run only)
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid

import structlog
from sqlalchemy import select

from src.agents.nodes.duplicate_detector import duplicate_detector_node
from src.agents.nodes.failure_classifier import failure_classifier_node
from src.agents.nodes.log_analyzer import log_analyzer_node
from src.agents.nodes.root_cause import root_cause_node
from src.agents.nodes.ticket_creator import ticket_creator_node
from src.agents.state import initial_state
from src.config.constants import CIProvider, PipelineStatus
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.repositories.ticket_repo import TicketRepository
from src.db.session import get_session_factory
from src.models.agent_run import AgentRun

logger = structlog.get_logger(__name__)


async def _create_synthetic_event_and_failure() -> tuple[str, str, str]:
    """Insert a synthetic PipelineEvent + TestFailure with spaces/unique token.

    Returns (pipeline_event_id, test_failure_id, unique_token) as strings.
    """
    session_factory = get_session_factory()
    unique_token = uuid.uuid4().hex
    test_name = f"dark mode toggle is visible and clickable - verification {unique_token[:8]}"

    async with session_factory() as session:
        pipeline_repo = PipelineEventRepository()
        failure_repo = FailureRepository()

        event = await pipeline_repo.create(
            session,
            provider=CIProvider.GITHUB_ACTIONS,
            provider_build_id=f"verify-jira-{unique_token[:12]}",
            repository="org/my-service",
            branch="main",
            commit_sha=unique_token[:40].ljust(40, "0"),
            pipeline_name="CI",
            status=PipelineStatus.FAILURE,
            raw_payload={"synthetic": True, "note": "verify_jira_ticket.py"},
        )

        failure = await failure_repo.create(
            session,
            pipeline_event_id=event.id,
            test_name=test_name,
            test_suite="tests.e2e.test_dark_mode",
            test_file="tests/e2e/test_dark_mode.spec.ts",
            error_message=(
                f"Error: Timed out waiting for selector '.dark-mode-toggle' "
                f"to be visible and clickable (token={unique_token})"
            ),
            stack_trace=(
                "Error: locator.click: Timeout 30000ms exceeded.\n"
                "  at DarkModeToggle.click (tests/e2e/test_dark_mode.spec.ts:42:21)\n"
                f"  Unique verification token: {unique_token}"
            ),
            duration_ms=31250,
        )

        # failure status starts as "new" (set by FailureRepository.create).
        # Bump pipeline status to "triaging" to mimic what pipeline_monitor
        # would normally have done before handing off to failure_classifier.
        await pipeline_repo.update_status(session, event.id, "triaging")
        await session.commit()

    return str(event.id), str(failure.id), unique_token


async def _print_ticket_or_error(test_failure_id: str) -> None:
    """Look up the TriageTicket for test_failure_id, or print the
    ticket_creator AgentRun's error if none was created."""
    session_factory = get_session_factory()
    failure_uuid = uuid.UUID(test_failure_id)

    async with session_factory() as session:
        ticket_repo = TicketRepository()
        ticket = await ticket_repo.get_by_failure_id(session, failure_uuid)

        if ticket is not None:
            print("\n=== Jira ticket created ===")
            print(f"  external_ticket_id : {ticket.external_ticket_id}")
            print(f"  external_url       : {ticket.external_url}")
            print(f"  title              : {ticket.title}")
            print(f"  priority           : {ticket.priority}")
            return

        print("\n=== No TriageTicket found ===")

        stmt = (
            select(AgentRun)
            .where(
                AgentRun.test_failure_id == failure_uuid,
                AgentRun.agent_name == "ticket_creator",
            )
            .order_by(AgentRun.started_at.desc())
        )
        result = await session.execute(stmt)
        runs = list(result.scalars().all())

        if not runs:
            print("  No ticket_creator AgentRun rows found for this failure.")
            return

        for run in runs:
            print(f"  agent_run id     : {run.id}")
            print(f"  status           : {run.status}")
            print(f"  input_summary    : {run.input_summary}")
            print(f"  output_summary   : {run.output_summary}")
            print()


async def _run_tail_pipeline(pipeline_event_id: str, test_failure_id: str) -> None:
    """Run failure_classifier -> log_analyzer -> root_cause ->
    duplicate_detector -> ticket_creator for a single synthetic failure.

    Deliberately skips pipeline_monitor_node -- see module docstring.
    """
    log = logger.bind(
        pipeline_event_id=pipeline_event_id, test_failure_id=test_failure_id
    )

    state = initial_state(pipeline_event_id)
    state["provider"] = CIProvider.GITHUB_ACTIONS
    state["repository"] = "org/my-service"
    state["branch"] = "main"
    state["failure_ids"] = [test_failure_id]
    state["current_failure_id"] = test_failure_id

    log.info("verify_jira_ticket.failure_classifier.start")
    state.update(await failure_classifier_node(state))  # type: ignore[typeddict-item]

    log.info("verify_jira_ticket.log_analyzer.start")
    state.update(await log_analyzer_node(state))  # type: ignore[typeddict-item]

    log.info("verify_jira_ticket.root_cause.start")
    state.update(await root_cause_node(state))  # type: ignore[typeddict-item]

    log.info("verify_jira_ticket.duplicate_detector.start")
    state.update(await duplicate_detector_node(state))  # type: ignore[typeddict-item]
    log.info(
        "verify_jira_ticket.duplicate_detector.result",
        is_duplicate=state.get("is_duplicate"),
        duplicate_of_id=state.get("duplicate_of_id"),
    )

    if state.get("is_duplicate", False):
        print(
            "\nWARNING: duplicate_detector flagged this synthetic failure as a "
            "duplicate (is_duplicate=True). ticket_creator_node will SKIP ticket "
            "creation. This should not happen given the unique token in the "
            "error message -- check error_signatures for a stale match."
        )
        return

    log.info("verify_jira_ticket.ticket_creator.start")
    state.update(await ticket_creator_node(state))  # type: ignore[typeddict-item]

    if state.get("errors"):
        print("\n=== Errors accumulated during pipeline run ===")
        for err in state["errors"]:
            print(f"  - {err}")


async def main_async(dry_run: bool) -> int:
    pipeline_event_id, test_failure_id, unique_token = (
        await _create_synthetic_event_and_failure()
    )

    print("Created synthetic records:")
    print(f"  pipeline_event_id : {pipeline_event_id}")
    print(f"  test_failure_id   : {test_failure_id}")
    print(f"  unique_token      : {unique_token}")

    if dry_run:
        print("\n--dry-run: stopping before graph invocation / LLM / Jira calls.")
        return 0

    await _run_tail_pipeline(pipeline_event_id, test_failure_id)
    await _print_ticket_or_error(test_failure_id)

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Verify ticket_creator_node's slugify_label fix against the real "
            "Jira API by running a synthetic test failure through "
            "failure_classifier -> log_analyzer -> root_cause -> "
            "duplicate_detector -> ticket_creator."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Only insert the synthetic PipelineEvent + TestFailure and print "
            "their IDs; do not invoke the graph, the LLM, or the Jira API."
        ),
    )
    args = parser.parse_args()

    exit_code = asyncio.run(main_async(dry_run=args.dry_run))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
