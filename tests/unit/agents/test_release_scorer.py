"""Tests for release_scorer_node() — mocked LLM + settings, real test DB."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.release_scorer import release_scorer_node
from src.agents.state import initial_state
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.release_score import ReleaseScore
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_pipeline_event(
    db_session: AsyncSession,
    commit_sha: str = "abc123",
    repository: str = "org/repo",
) -> PipelineEvent:
    """Insert a PipelineEvent into the test DB and return it."""
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id=f"run-{uuid.uuid4().hex[:8]}",
        repository=repository,
        branch="main",
        commit_sha=commit_sha,
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()
    return event


async def _make_failure(
    db_session: AsyncSession,
    event: PipelineEvent,
    category: str = "product_bug",
    confidence: float = 0.9,
    status: str = "triaged",
) -> TestFailure:
    """Insert a TestFailure + FailureClassification into the test DB and return the failure."""
    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name=f"test_feature_{uuid.uuid4().hex[:6]}",
        error_message="AssertionError: expected True, got False",
        stack_trace="Traceback (most recent call last):\n  File test.py, line 10\nAssertionError",
        status=status,
    )
    db_session.add(failure)
    await db_session.flush()

    classification = FailureClassification(
        test_failure_id=failure.id,
        category=category,
        confidence=confidence,
        reasoning="Automated classification for tests",
        model_used="claude-sonnet-4-20250514",
    )
    db_session.add(classification)
    await db_session.flush()
    return failure


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session.

    The node calls `session_factory()` twice — once for reads and once for the
    upsert.  Reusing the same session is safe because both calls operate inside
    the same test transaction.
    """

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


def _make_mock_llm(summary_text: str = "Low risk release.") -> MagicMock:
    """Build a mock ChatAnthropic whose .ainvoke() returns a mock with .content = summary_text."""
    mock_response = MagicMock()
    mock_response.content = summary_text

    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(return_value=mock_response)

    mock_cls = MagicMock(return_value=mock_llm_instance)
    return mock_cls


def _make_settings(release_score_claude_enabled: bool = False) -> MagicMock:
    """Return a mock settings object with sensible defaults."""
    settings = MagicMock()
    settings.release_score_claude_enabled = release_score_claude_enabled
    settings.default_model = "claude-sonnet-4-20250514"
    settings.anthropic_api_key = "test-key"
    return settings


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_release_scorer_no_commit_sha(db_session: AsyncSession):
    """Node returns release_score=None and makes no DB upsert when commit_sha is empty."""
    event = await _make_pipeline_event(db_session, commit_sha="")
    state = {**initial_state(str(event.id))}

    mock_settings = _make_settings()
    session_factory = _make_session_factory(db_session)

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
    ):
        result = await release_scorer_node(state)

    assert result["release_score"] is None

    # No ReleaseScore should have been written to the DB
    rows = list((await db_session.execute(select(ReleaseScore))).scalars().all())
    assert len(rows) == 0


async def test_release_scorer_low_risk(db_session: AsyncSession):
    """1 flaky_test failure produces a low risk score (flaky reduces raw_score)."""
    event = await _make_pipeline_event(db_session, commit_sha="lowrisk1")
    await _make_failure(db_session, event, category="flaky_test", confidence=0.7)

    state = {**initial_state(str(event.id))}
    mock_settings = _make_settings(release_score_claude_enabled=False)
    session_factory = _make_session_factory(db_session)

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
    ):
        result = await release_scorer_node(state)

    assert result["release_score"] is not None
    score_dict = result["release_score"]
    assert score_dict["risk_level"] == "low"
    assert score_dict["score"] < 20.0

    # Verify DB record was written with the correct risk_level
    stmt = select(ReleaseScore).where(
        ReleaseScore.commit_sha == "lowrisk1",
        ReleaseScore.repository == "org/repo",
    )
    db_record = (await db_session.execute(stmt)).scalar_one_or_none()
    assert db_record is not None
    assert db_record.risk_level == "low"


async def test_release_scorer_high_risk(db_session: AsyncSession):
    """3 product_bug + 2 infra failures (confidence 1.0) push score into high/critical territory.

    Score formula with these inputs:
      product_bugs:  min(3*20, 40) = 40
      infra:         min(2*10, 20) = 20
      raw_score:     60
      avg_confidence = 1.0  → score = 60  → risk_level = "high"
    """
    event = await _make_pipeline_event(db_session, commit_sha="highrisk1")
    for _ in range(3):
        await _make_failure(db_session, event, category="product_bug", confidence=1.0)
    for _ in range(2):
        await _make_failure(db_session, event, category="infra_issue", confidence=1.0)

    state = {**initial_state(str(event.id))}
    mock_settings = _make_settings(release_score_claude_enabled=False)
    session_factory = _make_session_factory(db_session)

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
    ):
        result = await release_scorer_node(state)

    assert result["release_score"] is not None
    score_dict = result["release_score"]
    assert score_dict["risk_level"] in ("high", "critical")
    assert score_dict["score"] >= 50.0


async def test_release_scorer_upserts_on_second_run(db_session: AsyncSession):
    """Running the node twice for the same commit_sha results in exactly 1 ReleaseScore row."""
    event = await _make_pipeline_event(db_session, commit_sha="same-sha")
    await _make_failure(db_session, event, category="product_bug", confidence=0.9)

    state = {**initial_state(str(event.id))}
    mock_settings = _make_settings(release_score_claude_enabled=False)
    session_factory = _make_session_factory(db_session)

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
    ):
        await release_scorer_node(state)
        await release_scorer_node(state)

    stmt = select(ReleaseScore).where(
        ReleaseScore.commit_sha == "same-sha",
        ReleaseScore.repository == "org/repo",
    )
    rows = list((await db_session.execute(stmt)).scalars().all())
    assert len(rows) == 1, f"Expected 1 ReleaseScore row, found {len(rows)}"


async def test_release_scorer_claude_disabled(db_session: AsyncSession):
    """When release_score_claude_enabled=False, ChatAnthropic is never instantiated."""
    event = await _make_pipeline_event(db_session, commit_sha="nodisable1")
    await _make_failure(db_session, event, category="env_issue", confidence=0.8)

    state = {**initial_state(str(event.id))}
    mock_settings = _make_settings(release_score_claude_enabled=False)
    session_factory = _make_session_factory(db_session)
    mock_llm_cls = _make_mock_llm()

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
        patch("src.agents.nodes.release_scorer.ChatAnthropic", mock_llm_cls),
    ):
        result = await release_scorer_node(state)

    # Claude must not have been instantiated
    mock_llm_cls.assert_not_called()

    # Template fallback must have produced a risk_summary
    assert result["release_score"] is not None
    assert result["release_score"]["risk_summary"] is not None
    assert len(result["release_score"]["risk_summary"]) > 0


async def test_release_scorer_claude_failure(db_session: AsyncSession):
    """When Claude raises an exception, the node does not raise and uses the template fallback."""
    event = await _make_pipeline_event(db_session, commit_sha="claudefail1")
    await _make_failure(db_session, event, category="product_bug", confidence=0.85)

    state = {**initial_state(str(event.id))}
    mock_settings = _make_settings(release_score_claude_enabled=True)
    session_factory = _make_session_factory(db_session)

    # Claude raises on ainvoke
    mock_response = MagicMock()
    mock_llm_instance = MagicMock()
    mock_llm_instance.ainvoke = AsyncMock(side_effect=Exception("API down"))
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch(
            "src.agents.nodes.release_scorer.get_session_factory",
            return_value=session_factory,
        ),
        patch(
            "src.agents.nodes.release_scorer.get_settings",
            return_value=mock_settings,
        ),
        patch("src.agents.nodes.release_scorer.ChatAnthropic", mock_llm_cls),
    ):
        result = await release_scorer_node(state)

    # Node must not propagate the exception — release_score is still populated
    assert result["release_score"] is not None

    # Template fallback must have produced a non-empty risk_summary
    assert result["release_score"]["risk_summary"] is not None
    assert len(result["release_score"]["risk_summary"]) > 0
