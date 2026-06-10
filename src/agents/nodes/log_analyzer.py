from __future__ import annotations

import hashlib
import re
import uuid

import structlog

from src.agents.nodes.run_tracking import finish_agent_run, start_agent_run
from src.agents.state import TriageState
from src.config.constants import AgentRunStatus
from src.db.repositories.failure_repo import FailureRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


def normalize_error(raw_error: str) -> str:
    """Apply all normalization steps in order and return the cleaned string.

    Steps applied:
    1. Strip ANSI escape codes
    2. Strip ISO 8601 timestamps (e.g. 2024-01-15T10:30:00.123Z)
    3. Strip HH:MM:SS timestamps
    4. Strip memory addresses (0x followed by 4+ hex digits)
    5. Strip "line N" references
    6. Strip UUIDs (8-4-4-4-12 hex format)
    7. Collapse whitespace
    """
    text = raw_error

    # Step 1: Strip ANSI escape codes
    text = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)

    # Step 2: Strip ISO 8601 timestamps (2024-01-15T10:30:00.123Z)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[\.\d]*Z?", "", text)

    # Step 3: Strip HH:MM:SS timestamps
    text = re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "", text)

    # Step 4: Strip memory addresses (0x followed by 4+ hex digits)
    text = re.sub(r"0x[0-9a-fA-F]{4,}", "", text)

    # Step 5: Strip "line N" references
    text = re.sub(r"\bline \d+\b", "", text)

    # Step 6: Strip UUIDs (8-4-4-4-12 lowercase hex)
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "",
        text,
    )

    # Step 7: Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def compute_signature(raw_error: str) -> str:
    """Normalize raw_error and return its SHA-256 hex digest."""
    normalized = normalize_error(raw_error)
    return hashlib.sha256(normalized.encode()).hexdigest()


async def log_analyzer_node(state: TriageState) -> dict:
    """Compute a stable error signature hash for each failure in state['failure_ids'].

    For every failure:
      1. Concatenate error_message and stack_trace.
      2. Normalize the combined text to strip volatile tokens.
      3. SHA-256 hash the normalized text.

    Returns a partial state dict with 'error_signature' set to the hash of the
    last processed failure.  The ErrorSignature DB record is created by the
    duplicate_detector node that follows.
    """
    log = logger.bind(
        node="log_analyzer",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("log_analyzer.started")

    if not state["failure_ids"]:
        log.warning("log_analyzer.no_failure_ids")
        return {"errors": state["errors"] + ["log_analyzer: no failure_ids"]}

    session_factory = get_session_factory()
    last_hash: str | None = None
    last_normalized: str | None = None
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        agent_run_id: uuid.UUID | None = None
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"log_analyzer: TestFailure not found: {failure_id}"
                    log.warning("log_analyzer.failure_not_found", failure_id=failure_id)
                    errors.append(msg)
                    continue

                agent_run_id = await start_agent_run(
                    session_factory,
                    test_failure_id=failure.id,
                    agent_name="log_analyzer",
                    input_summary=f"Test: {failure.test_name}",
                )

                error_text = (failure.error_message or "") + "\n" + (failure.stack_trace or "")
                normalized = normalize_error(error_text)
                sig_hash = hashlib.sha256(normalized.encode()).hexdigest()
                last_hash = sig_hash
                last_normalized = normalized

                log.info(
                    "log_analyzer.signature_computed",
                    failure_id=failure_id,
                    sig_hash=sig_hash,
                )
                await finish_agent_run(
                    session_factory,
                    agent_run_id,
                    status=AgentRunStatus.COMPLETED,
                    output_summary=f"signature={sig_hash}\nnormalized: {normalized[:300]}",
                )

        except Exception as exc:
            msg = f"log_analyzer: error processing {failure_id}: {exc}"
            log.warning(
                "log_analyzer.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)
            await finish_agent_run(
                session_factory,
                agent_run_id,
                status=AgentRunStatus.FAILED,
                output_summary=str(exc),
            )

    log.info("log_analyzer.complete", last_hash=last_hash)

    return {
        "error_signature": last_hash,
        "normalized_error_text": last_normalized,
        "errors": errors,
    }
