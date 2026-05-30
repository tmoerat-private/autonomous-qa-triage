"""Flaky test detector node.

Analyses historical failure and retry data for each test in the current
pipeline event and assigns a composite flakiness score.  Tests whose score
meets or exceeds settings.flaky_score_threshold are flagged as flaky so the
orchestrator can skip ticket creation and route them straight to the notifier.

Score formula (each component is 0.0-1.0):
  failure_rate_score = 1.0 if flaky_min <= failure_rate <= flaky_max else 0.0
  retry_score        = min(retry_rate * 2, 1.0)   # high retry rate ≈ flaky
  flakiness_score    = 0.6 * failure_rate_score + 0.4 * retry_score

A test must have at least FLAKY_MIN_SAMPLE_SIZE failures in the lookback
window before it is eligible for scoring (prevents false positives on tests
that failed only once or twice).
"""
from __future__ import annotations

import uuid

import structlog

from src.agents.state import TriageState
from src.config.constants import FLAKY_MIN_SAMPLE_SIZE
from src.config.settings import get_settings
from src.db.repositories.failure_repo import FailureRepository
from src.db.repositories.flakiness_repo import FlakynessRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


def _compute_flakiness_score(
    failure_rate: float,
    retry_rate: float,
    flaky_min: float,
    flaky_max: float,
) -> float:
    """Return a composite flakiness score in [0.0, 1.0].

    Args:
        failure_rate: Fraction of pipeline runs in which this test failed.
        retry_rate: Fraction of this test's runs that triggered a retry.
        flaky_min: Lower bound of the "flaky" failure-rate band.
        flaky_max: Upper bound of the "flaky" failure-rate band.

    Returns:
        Weighted composite score: 0.6 * failure_rate_score + 0.4 * retry_score.
    """
    failure_rate_score = 1.0 if flaky_min <= failure_rate <= flaky_max else 0.0
    retry_score = min(retry_rate * 2.0, 1.0)
    return 0.6 * failure_rate_score + 0.4 * retry_score


async def flaky_detector_node(state: TriageState) -> dict:
    """Detect statistically flaky tests using historical failure and retry data.

    For every failure_id in state['failure_ids']:
      1. Load the TestFailure record to obtain test_name and repository.
      2. Query FlakynessRepository for failure_count, total_runs, and retry_rate
         over the configured lookback window.
      3. Skip scoring if failure_count < FLAKY_MIN_SAMPLE_SIZE (too few samples).
      4. Compute a composite flakiness_score from failure_rate and retry_rate.
      5. If flakiness_score >= settings.flaky_score_threshold, flag the test.

    Returns a partial state dict with:
      - is_flaky: True if at least one test scored >= threshold.
      - flakiness_score: Score of the most flaky test found, or None.
      - flaky_test_names: Names of all tests flagged as flaky.
    """
    log = logger.bind(
        node="flaky_detector",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("flaky_detector.started")

    if not state["failure_ids"]:
        log.warning("flaky_detector.no_failure_ids")
        return {
            "is_flaky": False,
            "flakiness_score": None,
            "flaky_test_names": [],
        }

    settings = get_settings()
    session_factory = get_session_factory()
    flakiness_repo = FlakynessRepository()
    failure_repo = FailureRepository()

    highest_score: float | None = None
    flaky_test_names: list[str] = []
    errors: list[str] = list(state["errors"])

    lookback_days: int = settings.flaky_lookback_days
    score_threshold: float = settings.flaky_score_threshold
    flaky_min: float = settings.flaky_min_failure_rate
    flaky_max: float = settings.flaky_max_failure_rate

    for failure_id in state["failure_ids"]:
        try:
            async with session_factory() as session:
                failure = await failure_repo.get_by_id(
                    session, uuid.UUID(failure_id)
                )
                if failure is None:
                    msg = f"flaky_detector: TestFailure not found: {failure_id}"
                    log.warning(
                        "flaky_detector.failure_not_found",
                        failure_id=failure_id,
                    )
                    errors.append(msg)
                    continue

                test_name: str = failure.test_name
                repository: str | None = state.get("repository")

                # --- Gather statistical evidence ---
                failure_count: int = await flakiness_repo.get_failure_count_for_test(
                    session, test_name, repository, lookback_days
                )

                # Require a minimum sample size before scoring to avoid
                # false positives on tests that failed only once or twice.
                if failure_count < FLAKY_MIN_SAMPLE_SIZE:
                    log.info(
                        "flaky_detector.insufficient_sample",
                        failure_id=failure_id,
                        test_name=test_name,
                        failure_count=failure_count,
                        min_required=FLAKY_MIN_SAMPLE_SIZE,
                    )
                    continue

                total_runs: int = await flakiness_repo.get_total_pipeline_runs(
                    session, repository, lookback_days
                )
                failure_rate: float = (
                    failure_count / total_runs if total_runs > 0 else 0.0
                )

                retry_rate: float = await flakiness_repo.get_retry_rate_for_test(
                    session, test_name, repository, lookback_days
                )

                # --- Score ---
                score = _compute_flakiness_score(
                    failure_rate=failure_rate,
                    retry_rate=retry_rate,
                    flaky_min=flaky_min,
                    flaky_max=flaky_max,
                )

                log.info(
                    "flaky_detector.scored",
                    failure_id=failure_id,
                    test_name=test_name,
                    failure_rate=round(failure_rate, 4),
                    retry_rate=round(retry_rate, 4),
                    flakiness_score=round(score, 4),
                    threshold=score_threshold,
                )

                if score >= score_threshold:
                    flaky_test_names.append(test_name)
                    if highest_score is None or score > highest_score:
                        highest_score = score
                    log.info(
                        "flaky_detector.flaky_test_detected",
                        test_name=test_name,
                        score=round(score, 4),
                        failure_rate=round(failure_rate, 4),
                        retry_rate=round(retry_rate, 4),
                    )

        except Exception as exc:
            msg = f"flaky_detector: error processing {failure_id}: {exc}"
            log.warning(
                "flaky_detector.error",
                failure_id=failure_id,
                error=str(exc),
            )
            errors.append(msg)

    is_flaky = len(flaky_test_names) > 0

    log.info(
        "flaky_detector.complete",
        is_flaky=is_flaky,
        flaky_test_count=len(flaky_test_names),
        highest_score=highest_score,
    )

    return {
        "is_flaky": is_flaky,
        "flakiness_score": highest_score,
        "flaky_test_names": flaky_test_names,
        "errors": errors,
    }
