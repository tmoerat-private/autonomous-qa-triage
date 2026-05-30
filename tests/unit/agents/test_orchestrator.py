"""Tests for the LangGraph graph structure — no execution, no DB, no async needed."""
from __future__ import annotations

from src.agents.orchestrator import build_triage_graph, triage_graph
from src.agents.state import initial_state

# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------


def test_triage_graph_is_not_none():
    assert triage_graph is not None


def test_build_returns_compiled_graph():
    graph = build_triage_graph()
    assert graph is not None


def test_graph_has_all_nodes():
    """All four Sprint 2 node names are present in the compiled graph."""
    graph = build_triage_graph()
    # LangGraph CompiledStateGraph exposes node names via .nodes (a dict-like mapping)
    node_names = set(graph.nodes.keys())
    for expected in ("pipeline_monitor", "failure_classifier", "log_analyzer", "duplicate_detector"):
        assert expected in node_names, f"Expected node '{expected}' not found in {node_names}"


# ---------------------------------------------------------------------------
# initial_state
# ---------------------------------------------------------------------------


def test_initial_state_has_required_keys():
    state = initial_state("test-id")
    required_keys = (
        "pipeline_event_id",
        "failure_ids",
        "classification",
        "error_signature",
        "is_duplicate",
        "errors",
        "provider",
        "pipeline_name",
        "repository",
        "branch",
        "raw_logs",
        "parsed_failures",
        "current_failure_id",
        "current_failure",
        "duplicate_of_id",
        "ticket_id",
        "ticket_url",
        "notification_sent",
        "agent_run_id",
    )
    for key in required_keys:
        assert key in state, f"Expected key '{key}' missing from initial_state"


def test_initial_state_pipeline_event_id():
    state = initial_state("abc-123")
    assert state["pipeline_event_id"] == "abc-123"


def test_initial_state_zero_values():
    state = initial_state("any-id")
    assert state["failure_ids"] == []
    assert state["errors"] == []
    assert state["is_duplicate"] is False
    assert state["classification"] is None
