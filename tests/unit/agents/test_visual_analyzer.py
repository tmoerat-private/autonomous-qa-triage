"""Tests for visual_analyzer_node() — mocked LLM, real test DB, real temp files."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.visual_analyzer import VisualAnalysisResult, visual_analyzer_node
from src.agents.state import initial_state
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure
from src.models.test_screenshot import TestScreenshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure into the test DB and return the failure."""
    event = PipelineEvent(
        provider="jenkins",
        provider_build_id="build-visual-1",
        repository="org/repo",
        branch="main",
        commit_sha="abc123",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_login_ui",
        error_message="AssertionError: screenshot mismatch",
        stack_trace="File tests/ui/test_login.py, line 55\nAssertionError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


async def _make_screenshot(
    db_session: AsyncSession,
    failure_id,
    storage_path: str,
) -> TestScreenshot:
    """Insert a TestScreenshot record and return it."""
    screenshot = TestScreenshot(
        test_failure_id=failure_id,
        original_filename="test.png",
        content_type="image/png",
        storage_path=storage_path,
        file_size_bytes=100,
    )
    db_session.add(screenshot)
    await db_session.flush()
    return screenshot


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


def _make_mock_llm(
    has_regression: bool = False,
    confidence: float = 0.9,
) -> MagicMock:
    """Return a fully-configured ChatAnthropic mock returning a VisualAnalysisResult."""
    mock_result = VisualAnalysisResult(
        has_regression=has_regression,
        regression_description="Layout broken" if has_regression else None,
        affected_components=["Header"] if has_regression else [],
        confidence=confidence,
        comparison_note="Observed broken header" if has_regression else "No issues detected",
    )
    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(return_value=mock_result)

    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain

    mock_cls = MagicMock(return_value=mock_llm_instance)
    return mock_cls


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_visual_analyzer_no_screenshots(db_session: AsyncSession):
    """Node returns visual_analysis=None and empty screenshot_ids when no records exist in DB."""
    failure = await _make_failure(db_session)
    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm()
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is None
    assert result["screenshot_ids"] == []
    # Claude must never have been invoked
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()


async def test_visual_analyzer_regression_detected(
    db_session: AsyncSession, tmp_path
):
    """Node calls Claude and returns has_regression=True when a screenshot file exists."""
    failure = await _make_failure(db_session)

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    screenshot = await _make_screenshot(db_session, failure.id, str(img_path))

    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(has_regression=True, confidence=0.91)
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is not None
    assert result["visual_analysis"]["has_regression"] is True
    assert result["visual_analysis"]["confidence"] == pytest.approx(0.91)
    assert str(screenshot.id) in result["screenshot_ids"]
    assert len(result["screenshot_ids"]) >= 1


async def test_visual_analyzer_no_regression(
    db_session: AsyncSession, tmp_path
):
    """Node returns has_regression=False when Claude finds no visual issues."""
    failure = await _make_failure(db_session)

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    await _make_screenshot(db_session, failure.id, str(img_path))

    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm(has_regression=False, confidence=0.9)
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is not None
    assert result["visual_analysis"]["has_regression"] is False


async def test_visual_analyzer_file_missing(db_session: AsyncSession, tmp_path):
    """Node returns visual_analysis=None and appends an error when the file is missing on disk."""
    failure = await _make_failure(db_session)

    missing_path = str(tmp_path / "nonexistent.png")
    await _make_screenshot(db_session, failure.id, missing_path)

    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_llm_cls = _make_mock_llm()
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is None
    assert any("file missing" in err for err in result["errors"])
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()


async def test_visual_analyzer_claude_failure(db_session: AsyncSession, tmp_path):
    """Node captures Claude errors non-fatally: visual_analysis=None, error appended, no raise."""
    failure = await _make_failure(db_session)

    img_path = tmp_path / "test.png"
    img_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

    await _make_screenshot(db_session, failure.id, str(img_path))

    state = {**initial_state("some-event-id"), "failure_ids": [str(failure.id)]}

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("vision API error"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is None
    assert any("vision API error" in err for err in result["errors"])


async def test_visual_analyzer_empty_failure_ids(db_session: AsyncSession):
    """Node short-circuits immediately when failure_ids is empty, making no DB queries."""
    state = {**initial_state("some-event-id"), "failure_ids": []}

    mock_llm_cls = _make_mock_llm()
    session_factory = _make_session_factory(db_session)

    with (
        patch("src.agents.nodes.visual_analyzer.ChatAnthropic", mock_llm_cls),
        patch(
            "src.agents.nodes.visual_analyzer.get_session_factory",
            return_value=session_factory,
        ),
    ):
        result = await visual_analyzer_node(state)

    assert result["visual_analysis"] is None
    mock_llm_cls.return_value.with_structured_output.return_value.ainvoke.assert_not_called()
