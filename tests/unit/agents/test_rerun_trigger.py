"""Tests for rerun_trigger_node() — mocked CI clients, real test DB."""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.rerun_trigger import rerun_trigger_node
from src.agents.state import initial_state
from src.db.repositories.rerun_repo import RerunRepository
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_failure(
    db_session: AsyncSession,
    provider: str = "jenkins",
    pipeline_name: str = "build-tests",
    provider_build_id: str = "42",
    repository: str = "org/repo",
) -> tuple[PipelineEvent, TestFailure]:
    """Insert a PipelineEvent + TestFailure into the test DB and return both."""
    event = PipelineEvent(
        provider=provider,
        provider_build_id=provider_build_id,
        repository=repository,
        branch="main",
        commit_sha="abc123",
        pipeline_name=pipeline_name,
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_flaky_network_call",
        error_message="ConnectionError: intermittent timeout",
        stack_trace="File tests/test_api.py, line 55\nConnectionError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return event, failure


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


def _make_settings(enable_auto_rerun: bool = True) -> MagicMock:
    """Return a mock settings object."""
    settings = MagicMock()
    settings.enable_auto_rerun = enable_auto_rerun
    settings.jenkins_url = "https://jenkins.example.com"
    settings.jenkins_user = "admin"
    settings.jenkins_token = "token"
    settings.github_app_id = "gh-token"
    settings.default_model = "claude-sonnet-4-20250514"
    settings.anthropic_api_key = "test-key"
    return settings


def _make_jenkins_client_mock(
    job_name: str = "build-tests",
    build_number: int = 42,
) -> MagicMock:
    """Return a mock JenkinsClient usable as an async context manager."""
    mock_client = MagicMock()
    mock_client.trigger_rerun = AsyncMock(
        return_value={"triggered": True, "job_name": job_name, "build_number": build_number}
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_client)


def _make_github_client_mock(
    repo: str = "org/repo",
    run_id: int = 999,
) -> MagicMock:
    """Return a mock GitHubActionsClient usable as an async context manager."""
    mock_client = MagicMock()
    mock_client.trigger_rerun = AsyncMock(
        return_value={"triggered": True, "repo": repo, "run_id": run_id}
    )
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=mock_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_rerun_trigger_not_flaky(db_session: AsyncSession):
    """Node returns rerun_triggered=False when is_flaky is False."""
    event, failure = await _make_failure(db_session)
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": False,
    }
    mock_settings = _make_settings(enable_auto_rerun=True)

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is False

    # No DB record should have been written
    records = await RerunRepository().get_by_failure_id(db_session, failure.id)
    assert records == []


async def test_rerun_trigger_disabled(db_session: AsyncSession):
    """Node returns rerun_triggered=False when enable_auto_rerun is False."""
    event, failure = await _make_failure(db_session)
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": True,
    }
    mock_settings = _make_settings(enable_auto_rerun=False)
    mock_jenkins_cls = _make_jenkins_client_mock()

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
        patch("src.integrations.jenkins.client.JenkinsClient", mock_jenkins_cls),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is False
    # CI client trigger_rerun must never have been called
    mock_jenkins_cls.return_value.trigger_rerun.assert_not_called()


async def test_rerun_trigger_jenkins(db_session: AsyncSession):
    """Node triggers a Jenkins rerun and persists a RerunRequest record."""
    event, failure = await _make_failure(
        db_session,
        provider="jenkins",
        pipeline_name="build-tests",
        provider_build_id="42",
    )
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": True,
    }
    mock_settings = _make_settings(enable_auto_rerun=True)
    mock_jenkins_cls = _make_jenkins_client_mock(job_name="build-tests", build_number=42)

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
        patch("src.integrations.jenkins.client.JenkinsClient", mock_jenkins_cls),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is True
    assert result["rerun_job_id"] == "build-tests"

    # Verify a RerunRequest was written to DB
    records = await RerunRepository().get_by_failure_id(db_session, failure.id)
    assert len(records) == 1
    assert records[0].provider == "jenkins"
    assert records[0].trigger_reason == "flaky_detected"


async def test_rerun_trigger_github_actions(db_session: AsyncSession):
    """Node triggers a GitHub Actions rerun and returns the run_id as rerun_job_id."""
    event, failure = await _make_failure(
        db_session,
        provider="github_actions",
        pipeline_name=".github/workflows/ci.yml",
        provider_build_id="999",
        repository="org/repo",
    )
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": True,
    }
    mock_settings = _make_settings(enable_auto_rerun=True)
    mock_github_cls = _make_github_client_mock(repo="org/repo", run_id=999)

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
        patch("src.integrations.github_actions.client.GitHubActionsClient", mock_github_cls),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is True
    assert result["rerun_job_id"] == "999"


async def test_rerun_trigger_ci_failure(db_session: AsyncSession):
    """Node captures CI client exception into errors list without raising."""
    event, failure = await _make_failure(db_session, provider="jenkins")
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": True,
    }
    mock_settings = _make_settings(enable_auto_rerun=True)

    mock_client = MagicMock()
    mock_client.trigger_rerun = AsyncMock(side_effect=Exception("connection refused"))
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_jenkins_cls = MagicMock(return_value=mock_client)

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
        patch("src.integrations.jenkins.client.JenkinsClient", mock_jenkins_cls),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is False
    assert any("connection refused" in err for err in result["errors"])


async def test_rerun_trigger_unsupported_provider(db_session: AsyncSession):
    """Node returns rerun_triggered=False for an unsupported provider without crashing."""
    event, failure = await _make_failure(db_session, provider="circleci")
    state = {
        **initial_state(str(event.id)),
        "failure_ids": [str(failure.id)],
        "is_flaky": True,
    }
    mock_settings = _make_settings(enable_auto_rerun=True)

    with (
        patch("src.agents.nodes.rerun_trigger.get_settings", return_value=mock_settings),
        patch("src.agents.nodes.rerun_trigger.get_session_factory",
              return_value=_make_session_factory(db_session)),
    ):
        result = await rerun_trigger_node(state)

    assert result["rerun_triggered"] is False
