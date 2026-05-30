from __future__ import annotations

import uuid

import structlog

from src.agents.nodes.log_analyzer import compute_signature, normalize_error
from src.agents.state import TriageState
from src.agents.tools.vector_tools import find_similar_errors, store_error_embedding
from src.config.settings import get_settings
from src.db.repositories.error_signature_repo import ErrorSignatureRepository
from src.db.repositories.failure_repo import FailureRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


async def duplicate_detector_node(state: TriageState) -> dict:
    """Detect duplicate failures using a two-phase hybrid approach.

    Phase 1 — Exact hash match:
      For every failure in state['failure_ids']:
        1. Compute the SHA-256 signature hash of the normalized error text.
        2. Call ErrorSignatureRepository.get_or_create to look up or insert the hash.
        3. If the signature already existed (is_dup=True), record the first duplicate.
        4. Regardless of outcome, store the embedding in Qdrant and write the
           Qdrant point ID back to the DB via update_embedding_id.

    Phase 2 — Vector similarity (only when no exact duplicate found):
      5. Search Qdrant for similar embeddings above settings.similarity_threshold.
      6. If any result found, treat the closest match as a semantic duplicate.

    Returns a partial state dict with:
      - is_duplicate: True if any failure matched by hash or vector similarity.
      - duplicate_of_id: DB UUID string (exact match) or Qdrant point ID string
                         (vector match) of the first matched signature, or None.
    """
    log = logger.bind(
        node="duplicate_detector",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("duplicate_detector.started")

    if not state["failure_ids"]:
        log.warning("duplicate_detector.no_failure_ids")
        return {"is_duplicate": False, "duplicate_of_id": None}

    settings = get_settings()
    session_factory = get_session_factory()
    any_duplicate = False
    first_duplicate_id: str | None = None
    errors: list[str] = list(state["errors"])

    for failure_id in state["failure_ids"]:
        try:
            async with session_factory() as session:
                failure = await FailureRepository().get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"duplicate_detector: TestFailure not found: {failure_id}"
                    log.warning(
                        "duplicate_detector.failure_not_found",
                        failure_id=failure_id,
                    )
                    errors.append(msg)
                    continue

                error_text = (failure.error_message or "") + "\n" + (failure.stack_trace or "")
                sig_hash = compute_signature(error_text)
                normalized = normalize_error(error_text)

                # --- Phase 1: exact hash match ---
                sig, is_dup = await ErrorSignatureRepository().get_or_create(
                    session, sig_hash, normalized
                )

                # Always store the embedding for this signature in Qdrant and
                # write the point ID back to the DB row so future vector searches
                # can find it.  We do this for both new and existing signatures
                # so that any signature lacking an embedding gets backfilled.
                await store_error_embedding(
                    point_id=str(sig.id),
                    error_text=normalized,
                    payload={
                        "signature_hash": sig_hash,
                        "normalized_error": normalized[:500],
                    },
                )
                await ErrorSignatureRepository().update_embedding_id(
                    session, sig, str(sig.id)
                )
                await session.commit()

                if is_dup:
                    if first_duplicate_id is None:
                        any_duplicate = True
                        first_duplicate_id = str(sig.id)
                    log.info(
                        "duplicate_detector.exact_match_found",
                        failure_id=failure_id,
                        sig_hash=sig_hash,
                        duplicate_of_id=str(sig.id),
                    )
                    # Exact duplicate found — no need for vector search on this failure.
                    continue

                # --- Phase 2: vector similarity search (new signature only) ---
                similar = await find_similar_errors(
                    error_text=normalized,
                    score_threshold=settings.similarity_threshold,
                )

                if similar:
                    top = similar[0]
                    if first_duplicate_id is None:
                        any_duplicate = True
                        first_duplicate_id = top["id"]
                    log.info(
                        "duplicate_detector.vector_match_found",
                        failure_id=failure_id,
                        sig_hash=sig_hash,
                        matching_point_id=top["id"],
                        similarity_score=top["score"],
                    )
                else:
                    log.info(
                        "duplicate_detector.vector_no_match",
                        failure_id=failure_id,
                        sig_hash=sig_hash,
                        threshold=settings.similarity_threshold,
                    )

                log.info(
                    "duplicate_detector.checked",
                    failure_id=failure_id,
                    sig_hash=sig_hash,
                    is_duplicate=is_dup or bool(similar),
                )

        except Exception as exc:
            msg = f"duplicate_detector: error processing {failure_id}: {exc}"
            log.warning(
                "duplicate_detector.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)

    log.info(
        "duplicate_detector.complete",
        is_duplicate=any_duplicate,
        duplicate_of_id=first_duplicate_id,
    )

    return {
        "is_duplicate": any_duplicate,
        "duplicate_of_id": first_duplicate_id,
        "errors": errors,
    }
