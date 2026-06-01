from __future__ import annotations

from langgraph.graph import END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.nodes.duplicate_detector import duplicate_detector_node
from src.agents.nodes.failure_classifier import failure_classifier_node
from src.agents.nodes.flaky_detector import flaky_detector_node
from src.agents.nodes.heal_suggester import heal_suggester_node
from src.agents.nodes.learner import learner_node
from src.agents.nodes.log_analyzer import log_analyzer_node
from src.agents.nodes.notifier import notifier_node
from src.agents.nodes.pipeline_monitor import pipeline_monitor_node
from src.agents.nodes.rerun_trigger import rerun_trigger_node
from src.agents.nodes.root_cause import root_cause_node
from src.agents.nodes.ticket_creator import ticket_creator_node
from src.agents.nodes.visual_analyzer import visual_analyzer_node
from src.agents.state import TriageState


def route_after_dedup_and_flaky(state: TriageState) -> str:
    """Route based on duplicate and flakiness detection results.

    Priority order:
      1. Duplicate — always skip ticket creation, notify only.
      2. Flaky (not duplicate) — skip ticket creation, notify as flaky.
      3. Neither — create ticket then notify.
    """
    if state.get("is_duplicate", False):
        return "notifier"
    if state.get("is_flaky", False):
        return "notifier"
    return "ticket_creator"


def build_triage_graph() -> CompiledStateGraph:
    """Construct and compile the Phase 3 LangGraph triage pipeline.

    Graph topology (Phase 3):

        pipeline_monitor → failure_classifier → log_analyzer → visual_analyzer → root_cause
                                                                     ↓
                                                            heal_suggester
                                                                     ↓
                                                          duplicate_detector
                                                                     ↓
                                                             flaky_detector
                                                                     ↓
                                                            rerun_trigger
                                                                     ↓
                                                     route_after_dedup_and_flaky()
                                                     /          |           \\
                                               notifier     notifier    ticket_creator
                                           (is_duplicate) (is_flaky)   (neither)
                                                     \\          |          /
                                                      notifier ← ← ← ← ←
                                                           ↓
                                                        learner
                                                           ↓
                                                          END
    """
    graph: StateGraph = StateGraph(TriageState)

    graph.add_node("pipeline_monitor", pipeline_monitor_node)
    graph.add_node("failure_classifier", failure_classifier_node)
    graph.add_node("log_analyzer", log_analyzer_node)
    graph.add_node("visual_analyzer", visual_analyzer_node)
    graph.add_node("root_cause", root_cause_node)
    graph.add_node("heal_suggester", heal_suggester_node)
    graph.add_node("duplicate_detector", duplicate_detector_node)
    graph.add_node("flaky_detector", flaky_detector_node)
    graph.add_node("rerun_trigger", rerun_trigger_node)
    graph.add_node("ticket_creator", ticket_creator_node)
    graph.add_node("notifier", notifier_node)
    graph.add_node("learner", learner_node)

    graph.set_entry_point("pipeline_monitor")
    graph.add_edge("pipeline_monitor", "failure_classifier")
    graph.add_edge("failure_classifier", "log_analyzer")
    graph.add_edge("log_analyzer", "visual_analyzer")
    graph.add_edge("visual_analyzer", "root_cause")
    graph.add_edge("root_cause", "heal_suggester")
    graph.add_edge("heal_suggester", "duplicate_detector")
    graph.add_edge("duplicate_detector", "flaky_detector")
    graph.add_edge("flaky_detector", "rerun_trigger")
    graph.add_conditional_edges(
        "rerun_trigger",
        route_after_dedup_and_flaky,
        {"ticket_creator": "ticket_creator", "notifier": "notifier"},
    )
    graph.add_edge("ticket_creator", "notifier")
    graph.add_edge("notifier", "learner")
    graph.add_edge("learner", END)

    return graph.compile()


# Module-level singleton — compiled once at import time and reused for every
# triage invocation.  Each run supplies its own initial TriageState dict so
# there is no shared mutable state between concurrent executions.
triage_graph = build_triage_graph()
