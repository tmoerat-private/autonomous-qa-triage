"""Tests for environment_health_node() — mocked LLM, pure state dicts."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agents.nodes.environment_health import EnvironmentHealthResult, environment_health_node
from src.agents.state import initial_state
from src.config.settings import Settings

# ---------------------------------------------------------------------------
# Settings mock — prevents real env var lookups inside the node
# ---------------------------------------------------------------------------

_MOCK_SETTINGS = Settings(
    anthropic_api_key="test-key",
    default_model="claude-sonnet-4-6",
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _base_state(
    category: str | None = None,
    error_message: str = "",
    stack_trace: str = "",
    raw_logs: str = "",
) -> dict:
    """Build a minimal TriageState dict for environment_health_node tests."""
    state = initial_state("test-pipeline-event-id")
    if category is not None:
        state["classification"] = {"category": category, "confidence": 0.9, "reasoning": "test"}
    else:
        state["classification"] = None
    state["current_failure"] = {
        "error_message": error_message,
        "stack_trace": stack_trace,
    }
    state["raw_logs"] = raw_logs
    return state


def _make_mock_llm(is_healthy: bool, issues: list[str], confidence: float = 0.85) -> MagicMock:
    """Return a ChatAnthropic mock wired to return an EnvironmentHealthResult."""
    mock_result = EnvironmentHealthResult(
        is_healthy=is_healthy,
        issues=issues,
        confidence=confidence,
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


async def test_short_circuit_env_issue_classification():
    """State with classification category == 'env_issue' skips Claude entirely
    and returns environment_healthy=False immediately."""
    state = _base_state(category="env_issue")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert len(result["environment_issues"]) > 0
    # Claude should never have been instantiated
    mock_llm_cls.assert_not_called()


async def test_rule_connection_refused():
    """Error message containing 'connection refused' triggers rule-based detection."""
    state = _base_state(error_message="ConnectionError: connection refused on port 5432")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert any("connection" in issue.lower() for issue in result["environment_issues"])
    # Rule fired — no Claude call
    mock_llm_cls.assert_not_called()


async def test_rule_oomkilled():
    """Error message containing 'OOMKilled' triggers rule-based OOM detection."""
    state = _base_state(raw_logs="Container exited with OOMKilled signal: memory limit exceeded")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert any("memory" in issue.lower() or "oom" in issue.lower() for issue in result["environment_issues"])
    mock_llm_cls.assert_not_called()


async def test_rule_database_timeout():
    """Error containing both 'timeout' and 'database' triggers database timeout rule."""
    state = _base_state(error_message="timeout connecting to database after 30s")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert len(result["environment_issues"]) > 0
    mock_llm_cls.assert_not_called()


async def test_claude_returns_unhealthy():
    """When no rule fires, Claude returning is_healthy=False propagates correctly."""
    state = _base_state(error_message="AssertionError: unexpected Redis response")
    mock_llm_cls = _make_mock_llm(
        is_healthy=False,
        issues=["Redis unreachable"],
        confidence=0.9,
    )

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert "Redis unreachable" in result["environment_issues"]
    mock_llm_cls.assert_called_once()


async def test_claude_returns_healthy():
    """When no rule fires and Claude returns is_healthy=True, state reflects healthy."""
    state = _base_state(error_message="AssertionError: expected 99.99 but got 0.00")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[], confidence=0.95)

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is True
    assert result["environment_issues"] == []
    mock_llm_cls.assert_called_once()


async def test_claude_error_defaults_to_healthy():
    """When the Claude call raises an exception, the node gracefully defaults to healthy."""
    state = _base_state(error_message="AssertionError: value mismatch")

    mock_chain = MagicMock()
    mock_chain.ainvoke = AsyncMock(side_effect=Exception("Anthropic API unavailable"))
    mock_llm_instance = MagicMock()
    mock_llm_instance.with_structured_output.return_value = mock_chain
    mock_llm_cls = MagicMock(return_value=mock_llm_instance)

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    # Graceful fallback — should not raise, should default to healthy
    assert result["environment_healthy"] is True
    assert result["environment_issues"] == []


async def test_no_classification_in_state_does_not_short_circuit():
    """State with no classification field flows to rule/Claude check, not short-circuit."""
    # classification=None should not trigger the env_issue short-circuit
    state = _base_state(category=None, error_message="AssertionError: expected True")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    # Claude must have been called (not short-circuited)
    mock_llm_cls.assert_called_once()
    assert result["environment_healthy"] is True


async def test_rule_502_bad_gateway():
    """Error message containing '502 bad gateway' triggers upstream unavailable rule."""
    state = _base_state(raw_logs="upstream: 502 Bad Gateway returned from auth service")
    mock_llm_cls = _make_mock_llm(is_healthy=True, issues=[])

    with (
        patch("src.agents.nodes.environment_health.ChatAnthropic", mock_llm_cls),
        patch("src.agents.nodes.environment_health.get_settings", return_value=_MOCK_SETTINGS),
    ):
        result = await environment_health_node(state)

    assert result["environment_healthy"] is False
    assert len(result["environment_issues"]) > 0
    mock_llm_cls.assert_not_called()
