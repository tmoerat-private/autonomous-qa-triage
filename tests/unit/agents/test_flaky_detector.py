"""Tests for src/agents/nodes/flaky_detector.py.

All tests are pure unit tests — no real DB connections.
The session factory and repositories are mocked at the module boundary.

The score formula (from the module docstring):
  failure_rate_score = 1.0 if flaky_min <= failure_rate <= flaky_max else 0.0
  retry_score        = min(retry_rate * 2, 1.0)
  flakiness_score    = 0.6 * failure_rate_score + 0.4 * retry_score

Settings defaults (from constants.py / settings.py):
  FLAKY_MIN_SAMPLE_SIZE = 3
  flaky_score_threshold  = 0.5
  flaky_min_failure_rate = 0.05
  flaky_max_failure_rate = 0.75
  flaky_lookback_days    = 30
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.nodes.flaky_detector import _compute_flakiness_score, flaky_detector_node
from src.agents.state import initial_state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Default thresholds matching settings.py defaults.
_FLAKY_MIN = 0.05
_FLAKY_MAX = 0.75


def _make_failure(test_name: str = "test_example") -> MagicMock:
    failure = MagicMock()
    failure.test_name = test_name
    return failure


def _base_state(failure_ids: list[str], repository: str | None = "org/repo") -> dict:
    state = {**initial_state("test-pipeline-id"), "failure_ids": failure_ids}
    state["repository"] = repository
    return state


def _patch_repos(
    failure: MagicMock | None,
    failure_count: int = 10,
    total_runs: int = 30,
    retry_rate: float = 0.0,
) -> tuple:
    """Return a tuple of patchers for FailureRepository and FlakynessRepository."""
    mock_fail_repo = MagicMock()
    mock_fail_repo.get_by_id = AsyncMock(return_value=failure)

    mock_flakiness_repo = MagicMock()
    mock_flakiness_repo.get_failure_count_for_test = AsyncMock(return_value=failure_count)
    mock_flakiness_repo.get_total_pipeline_runs = AsyncMock(return_value=total_runs)
    mock_flakiness_repo.get_retry_rate_for_test = AsyncMock(return_value=retry_rate)

    return mock_fail_repo, mock_flakiness_repo


def _make_session_factory() -> MagicMock:
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(return_value=_ctx())
    return factory


# ---------------------------------------------------------------------------
# _compute_flakiness_score — pure-function unit tests (synchronous)
# ---------------------------------------------------------------------------


def test_score_zero_rates():
    """Both rates at zero produce a score of 0.0."""
    score = _compute_flakiness_score(
        failure_rate=0.0,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    assert score == 0.0


def test_score_failure_rate_in_band_no_retry():
    """failure_rate within [min, max] and retry_rate=0 → score = 0.6."""
    score = _compute_flakiness_score(
        failure_rate=0.3,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # failure_rate_score=1.0, retry_score=0.0 → 0.6*1.0 + 0.4*0.0 = 0.6
    assert score == pytest.approx(0.6, abs=1e-6)


def test_score_failure_rate_in_band_with_high_retry():
    """failure_rate in band and retry_rate=0.8 → retry_score clips to 1.0 → score=1.0."""
    score = _compute_flakiness_score(
        failure_rate=0.3,
        retry_rate=0.8,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # retry_score = min(0.8 * 2.0, 1.0) = min(1.6, 1.0) = 1.0
    # score = 0.6 * 1.0 + 0.4 * 1.0 = 1.0
    assert score == pytest.approx(1.0, abs=1e-6)


def test_score_failure_rate_in_band_partial_retry():
    """failure_rate in band and retry_rate=0.4 → score = 0.6 + 0.4*0.8 = 0.92."""
    score = _compute_flakiness_score(
        failure_rate=0.3,
        retry_rate=0.4,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # retry_score = min(0.4 * 2, 1.0) = 0.8
    # score = 0.6 * 1.0 + 0.4 * 0.8 = 0.6 + 0.32 = 0.92
    assert score == pytest.approx(0.92, abs=1e-6)


def test_score_failure_rate_above_max():
    """failure_rate above flaky_max → failure_rate_score=0 → score depends only on retry."""
    score = _compute_flakiness_score(
        failure_rate=0.9,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # failure_rate_score=0.0 (0.9 > 0.75), retry_score=0.0 → 0.0
    assert score == pytest.approx(0.0, abs=1e-6)


def test_score_failure_rate_below_min():
    """failure_rate below flaky_min → failure_rate_score=0 → score depends only on retry."""
    score = _compute_flakiness_score(
        failure_rate=0.01,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # failure_rate_score=0.0 (0.01 < 0.05), retry_score=0.0 → 0.0
    assert score == pytest.approx(0.0, abs=1e-6)


def test_score_at_lower_boundary():
    """failure_rate exactly at flaky_min is included in the band → score=0.6."""
    score = _compute_flakiness_score(
        failure_rate=_FLAKY_MIN,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    assert score == pytest.approx(0.6, abs=1e-6)


def test_score_at_upper_boundary():
    """failure_rate exactly at flaky_max is included in the band → score=0.6."""
    score = _compute_flakiness_score(
        failure_rate=_FLAKY_MAX,
        retry_rate=0.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    assert score == pytest.approx(0.6, abs=1e-6)


def test_retry_score_capped_at_one():
    """retry_rate > 0.5 clips retry_score to 1.0."""
    score = _compute_flakiness_score(
        failure_rate=0.3,
        retry_rate=1.0,
        flaky_min=_FLAKY_MIN,
        flaky_max=_FLAKY_MAX,
    )
    # retry_score = min(1.0 * 2, 1.0) = 1.0
    # score = 0.6 + 0.4 = 1.0
    assert score == pytest.approx(1.0, abs=1e-6)


# ---------------------------------------------------------------------------
# flaky_detector_node — integration tests (async, all IO mocked)
# ---------------------------------------------------------------------------


async def test_flaky_test_detected_above_threshold():
    """failure_count=10, total_runs=30 (rate=0.33), retry_rate=0.5 → is_flaky=True."""
    failure_id = str(uuid.uuid4())
    failure = _make_failure("test_login")
    mock_fail_repo, mock_flakiness_repo = _patch_repos(
        failure, failure_count=10, total_runs=30, retry_rate=0.5
    )
    factory = _make_session_factory()

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state([failure_id]))

    assert result["is_flaky"] is True
    assert "test_login" in result["flaky_test_names"]
    assert result["flakiness_score"] is not None
    assert result["flakiness_score"] > 0.5


async def test_below_min_sample_size_is_not_flaky():
    """failure_count below FLAKY_MIN_SAMPLE_SIZE(=3) → is_flaky=False regardless of rate."""
    failure_id = str(uuid.uuid4())
    failure = _make_failure("test_checkout")
    # failure_count=1 < 3 — should be skipped entirely.
    mock_fail_repo, mock_flakiness_repo = _patch_repos(
        failure, failure_count=1, total_runs=30, retry_rate=0.9
    )
    factory = _make_session_factory()

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state([failure_id]))

    assert result["is_flaky"] is False
    assert result["flakiness_score"] is None
    assert result["flaky_test_names"] == []


async def test_high_failure_rate_is_not_flaky():
    """failure_rate=0.83 (above flaky_max=0.75) → persistent bug, not flaky → is_flaky=False."""
    failure_id = str(uuid.uuid4())
    failure = _make_failure("test_payment")
    # 25/30 ≈ 0.83, above flaky_max → failure_rate_score=0.
    mock_fail_repo, mock_flakiness_repo = _patch_repos(
        failure, failure_count=25, total_runs=30, retry_rate=0.0
    )
    factory = _make_session_factory()

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state([failure_id]))

    assert result["is_flaky"] is False
    assert result["flaky_test_names"] == []


async def test_zero_failures_is_not_flaky():
    """failure_count=0 → not enough samples → is_flaky=False."""
    failure_id = str(uuid.uuid4())
    failure = _make_failure("test_empty")
    mock_fail_repo, mock_flakiness_repo = _patch_repos(
        failure, failure_count=0, total_runs=50, retry_rate=0.0
    )
    factory = _make_session_factory()

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state([failure_id]))

    assert result["is_flaky"] is False
    assert result["flakiness_score"] is None


async def test_empty_failure_ids_returns_not_flaky():
    """Node returns is_flaky=False immediately when failure_ids is empty."""
    result = await flaky_detector_node(_base_state([]))

    assert result["is_flaky"] is False
    assert result["flakiness_score"] is None
    assert result["flaky_test_names"] == []


async def test_flakiness_score_is_maximum_across_all_failures():
    """When multiple failures are scored, flakiness_score is the highest one."""
    failure_id_1 = str(uuid.uuid4())
    failure_id_2 = str(uuid.uuid4())

    failure_low = _make_failure("test_low_score")
    failure_high = _make_failure("test_high_score")

    # Simulate two consecutive get_by_id calls returning different failures.
    mock_fail_repo = MagicMock()
    mock_fail_repo.get_by_id = AsyncMock(side_effect=[failure_low, failure_high])

    # failure_low: failure_rate=0.2 (in band), retry_rate=0.0 → score=0.6
    # failure_high: failure_rate=0.3 (in band), retry_rate=0.4 → score=0.92
    mock_flakiness_repo = MagicMock()
    mock_flakiness_repo.get_failure_count_for_test = AsyncMock(side_effect=[6, 9])
    mock_flakiness_repo.get_total_pipeline_runs = AsyncMock(return_value=30)
    mock_flakiness_repo.get_retry_rate_for_test = AsyncMock(side_effect=[0.0, 0.4])

    # Each iteration opens a fresh context manager.
    mock_session = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(side_effect=_ctx)

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(
            _base_state([failure_id_1, failure_id_2])
        )

    assert result["is_flaky"] is True
    # Both tests scored above threshold; the highest score is retained.
    assert result["flakiness_score"] == pytest.approx(0.92, abs=1e-6)
    assert "test_low_score" in result["flaky_test_names"]
    assert "test_high_score" in result["flaky_test_names"]


async def test_flaky_test_names_populated_for_multiple_flaky_tests():
    """All tests that score >= threshold appear in flaky_test_names."""
    failure_ids = [str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())]
    failures = [
        _make_failure("test_alpha"),
        _make_failure("test_beta"),
        _make_failure("test_gamma"),
    ]

    mock_fail_repo = MagicMock()
    mock_fail_repo.get_by_id = AsyncMock(side_effect=failures)

    # All three have failure_rate in band and sufficient sample size.
    mock_flakiness_repo = MagicMock()
    mock_flakiness_repo.get_failure_count_for_test = AsyncMock(return_value=6)
    mock_flakiness_repo.get_total_pipeline_runs = AsyncMock(return_value=30)
    mock_flakiness_repo.get_retry_rate_for_test = AsyncMock(return_value=0.0)

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(side_effect=_ctx)

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state(failure_ids))

    assert result["is_flaky"] is True
    assert set(result["flaky_test_names"]) == {"test_alpha", "test_beta", "test_gamma"}


async def test_exception_on_one_failure_does_not_crash_node():
    """A DB error on one failure is caught and added to errors; the node continues."""
    failure_id_ok = str(uuid.uuid4())
    failure_id_bad = str(uuid.uuid4())

    good_failure = _make_failure("test_good")

    call_count = 0

    async def get_by_id_side_effect(session, uid):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("DB gone away")
        return good_failure

    mock_fail_repo = MagicMock()
    mock_fail_repo.get_by_id = AsyncMock(side_effect=get_by_id_side_effect)

    # The good failure has failure_count=10, total_runs=30, rate=0.33 — flaky.
    mock_flakiness_repo = MagicMock()
    mock_flakiness_repo.get_failure_count_for_test = AsyncMock(return_value=10)
    mock_flakiness_repo.get_total_pipeline_runs = AsyncMock(return_value=30)
    mock_flakiness_repo.get_retry_rate_for_test = AsyncMock(return_value=0.5)

    mock_session = AsyncMock()

    @asynccontextmanager
    async def _ctx():
        yield mock_session

    factory = MagicMock(side_effect=_ctx)

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(
            _base_state([failure_id_bad, failure_id_ok])
        )

    # Bad failure raised → error recorded.
    assert any("DB gone away" in err for err in result["errors"])
    # Good failure was still processed → detected as flaky.
    assert result["is_flaky"] is True
    assert "test_good" in result["flaky_test_names"]


async def test_failure_not_found_in_db_adds_error():
    """When get_by_id returns None, the failure is skipped and an error is appended."""
    failure_id = str(uuid.uuid4())
    mock_fail_repo = MagicMock()
    mock_fail_repo.get_by_id = AsyncMock(return_value=None)

    mock_flakiness_repo = MagicMock()
    factory = _make_session_factory()

    with (
        patch(
            "src.agents.nodes.flaky_detector.get_session_factory",
            return_value=factory,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FailureRepository",
            return_value=mock_fail_repo,
        ),
        patch(
            "src.agents.nodes.flaky_detector.FlakynessRepository",
            return_value=mock_flakiness_repo,
        ),
    ):
        result = await flaky_detector_node(_base_state([failure_id]))

    assert result["is_flaky"] is False
    assert any(failure_id in err for err in result["errors"])
