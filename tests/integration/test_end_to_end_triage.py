"""End-to-end integration tests for the Sprint 3 triage pipeline.

Real test DB, mocked LLM / Jira / Slack.  Pattern mirrors test_triage_pipeline.py.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.failure_classifier import ClassificationResult
from src.agents.state import TriageState
from src.models.error_signature import ErrorSignature
from src.models.failure_classification import FailureClassification
from src.models.notification import Notification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
from src.models.triage_ticket import TriageTicket
from src.services.triage_service import run_triage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_factory(test_session: AsyncSession):
    """Return a session-factory callable that always yields *test_session*."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


def _make_classifier_mock(category: str = "product_bug", confidence: float = 0.9) -> MagicMock:
    mock_result = ClassificationResult(
        category=category,
        confidence=confidence,
        reasoning="Mocked LLM reasoning",
    )
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_result)
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    return MagicMock(return_value=mock_llm_instance)


def _make_jira_mock(issue_key: str = "QA-1") -> MagicMock:
    """Return a JiraClient class mock that works as an async context manager."""
    mock_jira_instance = MagicMock()
    mock_jira_instance.create_issue = AsyncMock(
        return_value={
            "id": "10001",
            "key": issue_key,
            "url": f"https://jira.example.com/browse/{issue_key}",
        }
    )
    mock_jira_instance.__aenter__ = AsyncMock(return_value=mock_jira_instance)
    mock_jira_instance.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=mock_jira_instance)


def _make_slack_mock(ts: str = "1234567890.123456") -> MagicMock:
    """Return a SlackClient class mock (stateless, not a context manager)."""
    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = AsyncMock(
        return_value={"ok": True, "ts": ts}
    )
    return MagicMock(return_value=mock_slack_instance)


def _make_ticket_settings() -> MagicMock:
    s = MagicMock()
    s.jira_url = "https://jira.example.com"
    s.jira_project_key = "QA"
    s.jira_email = "test@example.com"
    s.jira_api_token = "token"
    return s


def _make_notifier_settings() -> MagicMock:
    s = MagicMock()
    s.slack_bot_token = "xoxb-test"
    s.slack_channel_id = "C123"
    return s


def _build_sprint3_graph(mock_pipeline_monitor_fn):
    """Compile a fresh 6-node triage graph using *mock_pipeline_monitor_fn*.

    Because triage_graph is a module-level singleton that captures function
    references at import time, patching the module name has no effect on the
    compiled graph.  Instead we build a fresh compiled graph here and replace
    ``src.services.triage_service.triage_graph`` with it so that run_triage()
    picks up the replacement.
    """
    from langgraph.graph import END, StateGraph

    from src.agents.nodes.duplicate_detector import duplicate_detector_node
    from src.agents.nodes.failure_classifier import failure_classifier_node
    from src.agents.nodes.log_analyzer import log_analyzer_node
    from src.agents.nodes.notifier import notifier_node
    from src.agents.nodes.ticket_creator import ticket_creator_node
    from src.agents.orchestrator import route_after_dedup_and_flaky as route_after_dedup

    graph: StateGraph = StateGraph(TriageState)
    graph.add_node("pipeline_monitor", mock_pipeline_monitor_fn)
    graph.add_node("failure_classifier", failure_classifier_node)
    graph.add_node("log_analyzer", log_analyzer_node)
    graph.add_node("duplicate_detector", duplicate_detector_node)
    graph.add_node("ticket_creator", ticket_creator_node)
    graph.add_node("notifier", notifier_node)
    graph.set_entry_point("pipeline_monitor")
    graph.add_edge("pipeline_monitor", "failure_classifier")
    graph.add_edge("failure_classifier", "log_analyzer")
    graph.add_edge("log_analyzer", "duplicate_detector")
    graph.add_conditional_edges(
        "duplicate_detector",
        route_after_dedup,
        {"ticket_creator": "ticket_creator", "notifier": "notifier"},
    )
    graph.add_edge("ticket_creator", "notifier")
    graph.add_edge("notifier", END)
    return graph.compile()


async def _insert_pipeline_event(
    db_session: AsyncSession,
    provider: str = "github_actions",
) -> PipelineEvent:
    event = PipelineEvent(
        provider=provider,
        provider_build_id="run-99",
        repository="org/repo",
        branch="main",
        commit_sha="abc123",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()
    return event


async def _insert_failure(
    db_session: AsyncSession,
    pipeline_event_id,
    error_message: str = "AssertionError: expected True but got False",
    stack_trace: str = "File test_feature.py, line 10\nAssertionError",
) -> TestFailure:
    failure = TestFailure(
        pipeline_event_id=pipeline_event_id,
        test_name="test_feature_flag",
        error_message=error_message,
        stack_trace=stack_trace,
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _all_node_patches(db_session: AsyncSession) -> list:
    """Patch get_session_factory for all 6 node modules."""
    factory = _make_session_factory(db_session)
    return [
        patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=factory),
        patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=factory),
        patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=factory),
        patch("src.agents.nodes.ticket_creator.get_session_factory", return_value=factory),
        patch("src.agents.nodes.notifier.get_session_factory", return_value=factory),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_full_pipeline_non_duplicate(db_session: AsyncSession):
    """Full happy-path: classify → analyze → new ticket → notify, all DB records created."""
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    session_factory = _make_session_factory(db_session)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_sprint3_graph(stub_pipeline_monitor)

    classifier_mock = _make_classifier_mock(category="product_bug", confidence=0.9)
    jira_mock = _make_jira_mock(issue_key="QA-1")
    slack_mock = _make_slack_mock()
    ticket_settings = _make_ticket_settings()
    notifier_settings = _make_notifier_settings()

    node_patches = _all_node_patches(db_session)

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        node_patches[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", jira_mock),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", slack_mock),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    # FailureClassification persisted
    cls_stmt = select(FailureClassification).where(
        FailureClassification.test_failure_id == failure.id
    )
    classification_row = (await db_session.execute(cls_stmt)).scalar_one_or_none()
    assert classification_row is not None
    assert classification_row.category == "product_bug"

    # ErrorSignature persisted
    sig_stmt = select(ErrorSignature)
    sigs = list((await db_session.execute(sig_stmt)).scalars().all())
    assert len(sigs) >= 1

    # TriageTicket persisted with the correct external key
    ticket_stmt = select(TriageTicket).where(TriageTicket.test_failure_id == failure.id)
    ticket_row = (await db_session.execute(ticket_stmt)).scalar_one_or_none()
    assert ticket_row is not None
    assert ticket_row.external_ticket_id == "QA-1"

    # Notification persisted
    notif_stmt = select(Notification).where(Notification.test_failure_id == failure.id)
    notif_row = (await db_session.execute(notif_stmt)).scalar_one_or_none()
    assert notif_row is not None

    # State keys
    assert result["notification_sent"] is True
    assert result["ticket_id"] == "QA-1"


async def test_full_pipeline_duplicate_skips_ticket(db_session: AsyncSession):
    """Second run with identical error text: is_duplicate=True, no second TriageTicket created."""
    same_error = "AssertionError: value mismatch in pipeline"
    same_trace = "File core.py, line 20\nAssertionError"

    event1 = await _insert_pipeline_event(db_session)
    failure1 = await _insert_failure(db_session, event1.id, error_message=same_error, stack_trace=same_trace)

    event2 = await _insert_pipeline_event(db_session)
    failure2 = await _insert_failure(db_session, event2.id, error_message=same_error, stack_trace=same_trace)

    session_factory = _make_session_factory(db_session)
    classifier_mock = _make_classifier_mock()
    ticket_settings = _make_ticket_settings()
    notifier_settings = _make_notifier_settings()

    async def stub_monitor_1(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure1.id)],
        }

    async def stub_monitor_2(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure2.id)],
        }

    graph1 = _build_sprint3_graph(stub_monitor_1)
    graph2 = _build_sprint3_graph(stub_monitor_2)

    node_patches = _all_node_patches(db_session)

    # First run — seeds ErrorSignature and creates ticket
    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        node_patches[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", _make_jira_mock("QA-1")),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", _make_slack_mock()),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", graph1),
    ):
        first_result = await run_triage(str(event1.id))

    assert first_result["is_duplicate"] is False

    # Re-build node patches so the factory closure is fresh for the second run
    node_patches2 = _all_node_patches(db_session)

    # Second run — should be detected as duplicate
    with (
        node_patches2[0],
        node_patches2[1],
        node_patches2[2],
        node_patches2[3],
        node_patches2[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", _make_jira_mock("QA-2")),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", _make_slack_mock()),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", graph2),
    ):
        second_result = await run_triage(str(event2.id))

    assert second_result["is_duplicate"] is True

    # Only one TriageTicket in the database (from the first run)
    all_tickets = list((await db_session.execute(select(TriageTicket))).scalars().all())
    assert len(all_tickets) == 1

    # The second failure still has a Notification
    notif_stmt = select(Notification).where(Notification.test_failure_id == failure2.id)
    notif_rows = list((await db_session.execute(notif_stmt)).scalars().all())
    assert len(notif_rows) >= 1


async def test_full_pipeline_jira_down_still_notifies(db_session: AsyncSession):
    """When Jira is unavailable, the notifier still runs and a Notification is persisted."""
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    session_factory = _make_session_factory(db_session)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_sprint3_graph(stub_pipeline_monitor)

    # Jira raises on create_issue
    exploding_jira_instance = MagicMock()
    exploding_jira_instance.create_issue = AsyncMock(side_effect=Exception("Jira unavailable"))
    exploding_jira_instance.__aenter__ = AsyncMock(return_value=exploding_jira_instance)
    exploding_jira_instance.__aexit__ = AsyncMock(return_value=False)
    exploding_jira_cls = MagicMock(return_value=exploding_jira_instance)

    classifier_mock = _make_classifier_mock()
    slack_mock = _make_slack_mock()
    ticket_settings = _make_ticket_settings()
    notifier_settings = _make_notifier_settings()
    node_patches = _all_node_patches(db_session)

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        node_patches[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", exploding_jira_cls),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", slack_mock),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    # Notification is created despite Jira failure
    notif_stmt = select(Notification).where(Notification.test_failure_id == failure.id)
    notif_rows = list((await db_session.execute(notif_stmt)).scalars().all())
    assert len(notif_rows) >= 1

    assert result["notification_sent"] is True
    assert len(result["errors"]) > 0


async def test_full_pipeline_slack_down_completes(db_session: AsyncSession):
    """When Slack is unavailable, run_triage() completes and TriageTicket is still created."""
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_sprint3_graph(stub_pipeline_monitor)

    classifier_mock = _make_classifier_mock()
    jira_mock = _make_jira_mock(issue_key="QA-5")

    # Slack raises on post_message
    exploding_slack_instance = MagicMock()
    exploding_slack_instance.post_message = AsyncMock(side_effect=Exception("Slack unavailable"))
    exploding_slack_cls = MagicMock(return_value=exploding_slack_instance)

    ticket_settings = _make_ticket_settings()
    notifier_settings = _make_notifier_settings()
    node_patches = _all_node_patches(db_session)

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        node_patches[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", jira_mock),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", exploding_slack_cls),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    # run_triage() must not raise
    assert isinstance(result, dict)

    assert result["notification_sent"] is False
    assert len(result["errors"]) > 0

    # TriageTicket was created before Slack failure
    ticket_stmt = select(TriageTicket).where(TriageTicket.test_failure_id == failure.id)
    ticket_row = (await db_session.execute(ticket_stmt)).scalar_one_or_none()
    assert ticket_row is not None
    assert ticket_row.external_ticket_id == "QA-5"


async def test_full_pipeline_result_has_all_keys(db_session: AsyncSession):
    """The result dict from run_triage() must contain all expected Sprint 3 state keys."""
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "github_actions",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_sprint3_graph(stub_pipeline_monitor)

    classifier_mock = _make_classifier_mock()
    jira_mock = _make_jira_mock()
    slack_mock = _make_slack_mock()
    ticket_settings = _make_ticket_settings()
    notifier_settings = _make_notifier_settings()
    node_patches = _all_node_patches(db_session)

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        node_patches[4],
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.ticket_creator.JiraClient", jira_mock),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=ticket_settings),
        patch("src.agents.nodes.notifier.SlackClient", slack_mock),
        patch("src.agents.nodes.notifier.get_settings", return_value=notifier_settings),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    required_keys = {
        "failure_ids",
        "classification",
        "error_signature",
        "is_duplicate",
        "ticket_id",
        "ticket_url",
        "notification_sent",
        "errors",
    }
    for key in required_keys:
        assert key in result, f"Missing key in result: {key}"
