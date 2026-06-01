from __future__ import annotations

from typing import TypedDict


class TriageState(TypedDict):
    # Input — set when the Celery task kicks off
    pipeline_event_id: str          # UUID as string

    # Set by pipeline_monitor node
    provider: str                   # CIProvider value
    pipeline_name: str | None
    repository: str | None
    branch: str | None
    raw_logs: str | None            # full console log text
    parsed_failures: list[dict]     # list of ParsedTestFailure.model_dump()
    failure_ids: list[str]          # UUIDs of saved TestFailure records

    # Set as each failure is processed
    current_failure_id: str | None
    current_failure: dict | None

    # Set by failure_classifier node (Sprint 2)
    classification: dict | None     # {category, confidence, reasoning}

    # Set by log_analyzer node (Sprint 2)
    error_signature: str | None     # SHA-256 hash
    normalized_error_text: str | None  # normalized text used to compute the hash

    # Set by duplicate_detector node (Sprint 2)
    is_duplicate: bool
    duplicate_of_id: str | None

    # Set by flaky_detector node (Phase 2)
    is_flaky: bool
    flakiness_score: float | None
    flaky_test_names: list[str]

    # Set by ticket_creator node (Sprint 3)
    ticket_id: str | None
    ticket_url: str | None

    # Set by notifier node (Sprint 3)
    notification_sent: bool

    # Set by root_cause node (Phase 3)
    root_cause: dict | None

    # Set by heal_suggester node (Phase 3)
    heal_suggestion: dict | None

    # Set by rerun_trigger node (Phase 3)
    rerun_triggered: bool
    rerun_job_id: str | None

    # Observability
    agent_run_id: str | None
    errors: list[str]               # non-fatal errors accumulated during triage


def initial_state(pipeline_event_id: str) -> TriageState:
    """Return a TriageState with all optional fields set to their zero values.

    The caller only needs to supply the ``pipeline_event_id``; every other
    field is initialised to ``None``, ``[]``, or ``False`` as appropriate.
    LangGraph merges partial dicts returned by each node into this base state.
    """
    return TriageState(
        pipeline_event_id=pipeline_event_id,
        # pipeline_monitor outputs
        provider="",
        pipeline_name=None,
        repository=None,
        branch=None,
        raw_logs=None,
        parsed_failures=[],
        failure_ids=[],
        # per-failure processing
        current_failure_id=None,
        current_failure=None,
        # failure_classifier outputs (Sprint 2)
        classification=None,
        # log_analyzer outputs (Sprint 2)
        error_signature=None,
        normalized_error_text=None,
        # duplicate_detector outputs (Sprint 2)
        is_duplicate=False,
        duplicate_of_id=None,
        # flaky_detector outputs (Phase 2)
        is_flaky=False,
        flakiness_score=None,
        flaky_test_names=[],
        # ticket_creator outputs (Sprint 3)
        ticket_id=None,
        ticket_url=None,
        # notifier outputs (Sprint 3)
        notification_sent=False,
        # root_cause outputs (Phase 3)
        root_cause=None,
        # heal_suggester outputs (Phase 3)
        heal_suggestion=None,
        # rerun_trigger outputs (Phase 3)
        rerun_triggered=False,
        rerun_job_id=None,
        # observability
        agent_run_id=None,
        errors=[],
    )
