"""Tests for Phase 2 orchestrator routing and graph structure.

Covers:
- route_after_dedup_and_flaky() — all three routing outcomes
- build_triage_graph() compilation
- Node presence including the new flaky_detector node
- Edge topology: duplicate_detector → flaky_detector → conditional branch

Graph introspection approach:
  compiled.nodes        — Pregel runtime nodes (dict[str, PregelNode])
  compiled.builder.nodes — original StateGraph nodes (user-supplied names only)
  compiled.builder.edges — set[tuple[str, str]] of regular (non-conditional) edges
"""
from __future__ import annotations

from src.agents.orchestrator import build_triage_graph, route_after_dedup_and_flaky
from src.agents.state import initial_state

# ---------------------------------------------------------------------------
# route_after_dedup_and_flaky — pure function, synchronous
# ---------------------------------------------------------------------------


def test_route_duplicate_not_flaky_goes_to_notifier():
    """Duplicate failure skips ticket creation and routes straight to notifier."""
    state = {
        **initial_state("test-event"),
        "is_duplicate": True,
        "is_flaky": False,
    }
    assert route_after_dedup_and_flaky(state) == "notifier"


def test_route_duplicate_and_flaky_duplicate_takes_priority():
    """When both is_duplicate and is_flaky are True, duplicate takes priority
    and the route is still 'notifier' (both paths converge, but duplicate wins)."""
    state = {
        **initial_state("test-event"),
        "is_duplicate": True,
        "is_flaky": True,
    }
    assert route_after_dedup_and_flaky(state) == "notifier"


def test_route_flaky_not_duplicate_goes_to_notifier():
    """Flaky-but-not-duplicate failure skips ticket creation → notifier."""
    state = {
        **initial_state("test-event"),
        "is_duplicate": False,
        "is_flaky": True,
    }
    assert route_after_dedup_and_flaky(state) == "notifier"


def test_route_not_duplicate_not_flaky_goes_to_ticket_creator():
    """Novel, non-flaky failure routes to ticket_creator."""
    state = {
        **initial_state("test-event"),
        "is_duplicate": False,
        "is_flaky": False,
    }
    assert route_after_dedup_and_flaky(state) == "ticket_creator"


def test_route_default_initial_state_goes_to_ticket_creator():
    """initial_state defaults (is_duplicate=False, is_flaky=False) routes to ticket_creator."""
    state = initial_state("test-event")
    assert route_after_dedup_and_flaky(state) == "ticket_creator"


# ---------------------------------------------------------------------------
# Graph compilation
# ---------------------------------------------------------------------------


def test_build_triage_graph_compiles_without_error():
    """build_triage_graph() must not raise."""
    graph = build_triage_graph()
    assert graph is not None


def test_compiled_graph_is_not_none_module_level():
    """The module-level singleton triage_graph is available after import."""
    from src.agents.orchestrator import triage_graph
    assert triage_graph is not None


# ---------------------------------------------------------------------------
# Node presence
# ---------------------------------------------------------------------------


EXPECTED_NODES = {
    "pipeline_monitor",
    "failure_classifier",
    "log_analyzer",
    "duplicate_detector",
    "flaky_detector",
    "ticket_creator",
    "notifier",
    "learner",
}


def test_graph_contains_all_phase2_nodes():
    """All Phase 2 nodes including flaky_detector are present in the compiled graph."""
    graph = build_triage_graph()
    # compiled.nodes is the Pregel runtime dict; it also contains __start__.
    node_names = set(graph.nodes.keys())
    for expected in EXPECTED_NODES:
        assert expected in node_names, (
            f"Expected node '{expected}' not found. Available: {node_names}"
        )


def test_graph_builder_nodes_match_expected():
    """builder.nodes (StateGraph level) contains exactly the expected user-defined nodes."""
    graph = build_triage_graph()
    builder_node_names = set(graph.builder.nodes.keys())
    for expected in EXPECTED_NODES:
        assert expected in builder_node_names, (
            f"Expected builder node '{expected}' not found in {builder_node_names}"
        )


def test_flaky_detector_node_is_present():
    """Explicit check: flaky_detector is in the compiled graph."""
    graph = build_triage_graph()
    assert "flaky_detector" in graph.nodes


# ---------------------------------------------------------------------------
# Edge topology
# ---------------------------------------------------------------------------


def test_duplicate_detector_leads_to_flaky_detector():
    """There is a direct edge from duplicate_detector to flaky_detector."""
    graph = build_triage_graph()
    # builder.edges is a set of (start, end) string tuples.
    edges = graph.builder.edges
    assert ("duplicate_detector", "flaky_detector") in edges, (
        f"Expected edge (duplicate_detector, flaky_detector) not found. "
        f"Edges: {edges}"
    )


def test_pipeline_monitor_leads_to_failure_classifier():
    """pipeline_monitor → failure_classifier edge is present."""
    graph = build_triage_graph()
    assert ("pipeline_monitor", "failure_classifier") in graph.builder.edges


def test_failure_classifier_leads_to_log_analyzer():
    """failure_classifier → log_analyzer edge is present."""
    graph = build_triage_graph()
    assert ("failure_classifier", "log_analyzer") in graph.builder.edges


def test_log_analyzer_leads_to_duplicate_detector():
    """log_analyzer → duplicate_detector edge is present."""
    graph = build_triage_graph()
    assert ("log_analyzer", "duplicate_detector") in graph.builder.edges


def test_ticket_creator_leads_to_notifier():
    """ticket_creator → notifier edge is present."""
    graph = build_triage_graph()
    assert ("ticket_creator", "notifier") in graph.builder.edges


def test_flaky_detector_has_no_unconditional_outgoing_edge():
    """flaky_detector exits only via a conditional branch, not a plain edge."""
    graph = build_triage_graph()
    outgoing_plain = {end for start, end in graph.builder.edges if start == "flaky_detector"}
    # Plain edges from flaky_detector must be empty — routing is conditional.
    assert outgoing_plain == set(), (
        f"Expected no unconditional edges from flaky_detector, found: {outgoing_plain}"
    )


def test_notifier_leads_to_learner():
    """notifier must have a direct edge to learner (not straight to END)."""
    graph = build_triage_graph()
    assert ("notifier", "learner") in graph.builder.edges, (
        f"Expected (notifier, learner) edge. Edges from notifier: "
        f"{[e for e in graph.builder.edges if e[0] == 'notifier']}"
    )


def test_learner_is_terminal_node():
    """learner is the last user-defined node; it has no plain edges to other user nodes."""
    graph = build_triage_graph()
    outgoing = {end for start, end in graph.builder.edges if start == "learner"}
    for dest in outgoing:
        assert dest not in EXPECTED_NODES, (
            f"learner unexpectedly routes to user node '{dest}'"
        )


def test_learner_node_is_present():
    """Explicit check: learner is in the compiled graph."""
    graph = build_triage_graph()
    assert "learner" in graph.nodes
