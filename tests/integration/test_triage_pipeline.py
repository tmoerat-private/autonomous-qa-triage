"""Integration tests for run_triage() — real test DB, mocked LLM and CI clients."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.failure_classifier import ClassificationResult
from src.agents.state import TriageState
from src.models.error_signature import ErrorSignature
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
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


def _make_classifier_mock(category: str = "product_bug", confidence: float = 0.85) -> MagicMock:
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


def _make_jenkins_client_mock() -> MagicMock:
    """Return a mock JenkinsClient that works as an async context manager."""
    mock_client = MagicMock()
    mock_client.get_build_logs_for = AsyncMock(return_value="")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


def _make_github_client_mock() -> MagicMock:
    """Return a mock GitHubActionsClient that works as an async context manager."""
    mock_client = MagicMock()
    mock_client.get_build_logs = AsyncMock(return_value="")
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls


async def _insert_pipeline_event(
    db_session: AsyncSession,
    provider: str = "jenkins",
    raw_payload: dict | None = None,
) -> PipelineEvent:
    event = PipelineEvent(
        provider=provider,
        provider_build_id="build-99",
        repository="org/repo",
        branch="main",
        commit_sha="abc123",
        pipeline_name="CI",
        status="failure",
        raw_payload=raw_payload or {},
    )
    db_session.add(event)
    await db_session.flush()
    return event


async def _insert_failure(
    db_session: AsyncSession,
    pipeline_event_id,
    error_message: str = "AssertionError: Expected 200, got 500",
    stack_trace: str = "File test.py, line 42\nAssertionError",
) -> TestFailure:
    failure = TestFailure(
        pipeline_event_id=pipeline_event_id,
        test_name="test_checkout",
        error_message=error_message,
        stack_trace=stack_trace,
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _build_patched_graph(mock_pipeline_monitor_fn):
    """Compile a fresh triage graph using *mock_pipeline_monitor_fn* in place of the real node.

    Because triage_graph is a module-level singleton that captures function references at
    import time, patching the module name has no effect on the compiled graph.  Instead we
    build a new compiled graph here and patch `src.services.triage_service.triage_graph`
    with it so that run_triage() picks up the replacement.
    """
    from langgraph.graph import END, StateGraph

    from src.agents.nodes.duplicate_detector import duplicate_detector_node
    from src.agents.nodes.failure_classifier import failure_classifier_node
    from src.agents.nodes.log_analyzer import log_analyzer_node

    graph: StateGraph = StateGraph(TriageState)
    graph.add_node("pipeline_monitor", mock_pipeline_monitor_fn)
    graph.add_node("failure_classifier", failure_classifier_node)
    graph.add_node("log_analyzer", log_analyzer_node)
    graph.add_node("duplicate_detector", duplicate_detector_node)
    graph.set_entry_point("pipeline_monitor")
    graph.add_edge("pipeline_monitor", "failure_classifier")
    graph.add_edge("failure_classifier", "log_analyzer")
    graph.add_edge("log_analyzer", "duplicate_detector")
    graph.add_edge("duplicate_detector", END)
    return graph.compile()


def _all_node_patches(db_session: AsyncSession) -> list:
    """Return patch() context managers for all four nodes' get_session_factory calls."""
    factory = _make_session_factory(db_session)
    return [
        patch("src.agents.nodes.pipeline_monitor.get_session_factory", return_value=factory),
        patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=factory),
        patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=factory),
        patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=factory),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_run_triage_no_failures(db_session: AsyncSession):
    """PipelineEvent with no TestFailures → failure_ids == [].

    Uses the real pipeline_monitor_node with mocked CI clients so no logs are fetched,
    no failures are parsed, and failure_ids remains empty.
    """
    event = await _insert_pipeline_event(db_session)

    node_patches = _all_node_patches(db_session)
    jenkins_mock = _make_jenkins_client_mock()
    github_mock = _make_github_client_mock()
    classifier_mock = _make_classifier_mock()

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        patch("src.agents.nodes.pipeline_monitor.JenkinsClient", jenkins_mock),
        patch("src.agents.nodes.pipeline_monitor.GitHubActionsClient", github_mock),
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
    ):
        result = await run_triage(str(event.id))

    assert result["failure_ids"] == []


async def test_run_triage_classifies_and_creates_signature(db_session: AsyncSession):
    """Full pipeline persists FailureClassification and ErrorSignature; is_duplicate is False.

    A fresh graph is compiled with a stub pipeline_monitor that injects the pre-seeded
    failure_id, so the real classifier, log_analyzer, and duplicate_detector run against
    the test DB.
    """
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    session_factory = _make_session_factory(db_session)
    classifier_mock = _make_classifier_mock(category="product_bug", confidence=0.9)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "jenkins",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_patched_graph(stub_pipeline_monitor)

    with (
        patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    # FailureClassification record should exist
    stmt = select(FailureClassification).where(
        FailureClassification.test_failure_id == failure.id
    )
    row = (await db_session.execute(stmt)).scalar_one_or_none()
    assert row is not None
    assert row.category == "product_bug"

    # ErrorSignature record should exist
    sig_stmt = select(ErrorSignature)
    sigs = list((await db_session.execute(sig_stmt)).scalars().all())
    assert len(sigs) >= 1

    assert result["is_duplicate"] is False


async def test_run_triage_detects_duplicate(db_session: AsyncSession):
    """Second run for a failure with the same error text sets is_duplicate == True.

    Both runs use a stub pipeline_monitor that injects the respective pre-seeded failure_id.
    The duplicate_detector computes identical hashes for identical error text, so the second
    run is flagged as a duplicate.
    """
    same_error = "AssertionError: value mismatch"
    same_trace = "File core.py, line 10\nAssertionError"

    event1 = await _insert_pipeline_event(db_session)
    failure1 = await _insert_failure(
        db_session, event1.id, error_message=same_error, stack_trace=same_trace
    )
    event2 = await _insert_pipeline_event(db_session)
    failure2 = await _insert_failure(
        db_session, event2.id, error_message=same_error, stack_trace=same_trace
    )

    session_factory = _make_session_factory(db_session)
    classifier_mock = _make_classifier_mock()

    async def stub_monitor_1(state: TriageState) -> dict:
        return {
            "provider": "jenkins",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure1.id)],
        }

    async def stub_monitor_2(state: TriageState) -> dict:
        return {
            "provider": "jenkins",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure2.id)],
        }

    graph1 = _build_patched_graph(stub_monitor_1)
    graph2 = _build_patched_graph(stub_monitor_2)

    common_patches = dict(
        classifier=patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        classifier_sf=patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=session_factory),
        log_sf=patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=session_factory),
        dup_sf=patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=session_factory),
    )

    # First run — seeds the ErrorSignature
    with (
        common_patches["classifier"],
        common_patches["classifier_sf"],
        common_patches["log_sf"],
        common_patches["dup_sf"],
        patch("src.services.triage_service.triage_graph", graph1),
    ):
        first_result = await run_triage(str(event1.id))

    assert first_result["is_duplicate"] is False

    # Second run — should detect the duplicate
    with (
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
        patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=session_factory),
        patch("src.services.triage_service.triage_graph", graph2),
    ):
        second_result = await run_triage(str(event2.id))

    assert second_result["is_duplicate"] is True


async def test_run_triage_errors_do_not_raise(db_session: AsyncSession):
    """When the LLM raises, run_triage completes without re-raising; errors list is non-empty.

    A stub pipeline_monitor injects the pre-seeded failure so the classifier actually
    runs and hits the mocked exception.
    """
    event = await _insert_pipeline_event(db_session)
    failure = await _insert_failure(db_session, event.id)

    session_factory = _make_session_factory(db_session)

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("boom"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    exploding_classifier = MagicMock(return_value=mock_llm_instance)

    async def stub_pipeline_monitor(state: TriageState) -> dict:
        return {
            "provider": "jenkins",
            "pipeline_name": "CI",
            "repository": "org/repo",
            "branch": "main",
            "raw_logs": "",
            "parsed_failures": [],
            "failure_ids": [str(failure.id)],
        }

    patched_graph = _build_patched_graph(stub_pipeline_monitor)

    with (
        patch("src.agents.nodes.failure_classifier.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.log_analyzer.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.duplicate_detector.get_session_factory", return_value=session_factory),
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", exploding_classifier),
        patch("src.services.triage_service.triage_graph", patched_graph),
    ):
        result = await run_triage(str(event.id))

    assert isinstance(result, dict)
    assert len(result.get("errors", [])) > 0


async def test_run_triage_returns_dict(db_session: AsyncSession):
    """run_triage() always returns a plain dict, regardless of what LangGraph wraps it in."""
    event = await _insert_pipeline_event(db_session)

    node_patches = _all_node_patches(db_session)
    classifier_mock = _make_classifier_mock()
    jenkins_mock = _make_jenkins_client_mock()
    github_mock = _make_github_client_mock()

    with (
        node_patches[0],
        node_patches[1],
        node_patches[2],
        node_patches[3],
        patch("src.agents.nodes.pipeline_monitor.JenkinsClient", jenkins_mock),
        patch("src.agents.nodes.pipeline_monitor.GitHubActionsClient", github_mock),
        patch("src.agents.nodes.failure_classifier.ChatAnthropic", classifier_mock),
    ):
        result = await run_triage(str(event.id))

    assert type(result) is dict
