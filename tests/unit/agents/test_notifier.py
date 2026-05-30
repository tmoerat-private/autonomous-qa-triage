"""Tests for notifier_node() — mocked SlackClient, real test DB."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.notifier import notifier_node
from src.agents.state import initial_state
from src.db.repositories.notification_repo import NotificationRepository
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers (mirror pattern from test_failure_classifier.py)
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure into the test DB and return the failure."""
    event = PipelineEvent(
        provider="github_actions",
        provider_build_id="run-42",
        repository="org/repo",
        branch="main",
        commit_sha="def456",
        pipeline_name="CI",
        status="failure",
        raw_payload={},
    )
    db_session.add(event)
    await db_session.flush()

    failure = TestFailure(
        pipeline_event_id=event.id,
        test_name="test_payment_flow",
        error_message="TimeoutError: request timed out after 30s",
        stack_trace="File payments.py, line 55\nTimeoutError",
        status="new",
    )
    db_session.add(failure)
    await db_session.flush()
    return failure


def _make_session_factory(test_session: AsyncSession):
    """Return a callable that produces an async context manager yielding test_session."""

    @asynccontextmanager
    async def _ctx():
        yield test_session

    def _factory():
        return _ctx()

    return _factory


def _make_slack_mock(ts: str = "1234567890.123456") -> MagicMock:
    """Return a SlackClient class mock (stateless, not a context manager)."""
    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = AsyncMock(
        return_value={"ok": True, "ts": ts}
    )
    mock_slack_cls = MagicMock(return_value=mock_slack_instance)
    return mock_slack_cls


def _make_settings(
    slack_bot_token: str = "xoxb-test",
    slack_channel_id: str = "C123",
) -> MagicMock:
    s = MagicMock()
    s.slack_bot_token = slack_bot_token
    s.slack_channel_id = slack_channel_id
    return s


def _build_state(failure: TestFailure, **overrides) -> dict:
    state = {
        **initial_state("test-event-id"),
        "failure_ids": [str(failure.id)],
        "repository": "org/repo",
        "branch": "main",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_notifier_happy_path(db_session: AsyncSession):
    """Node sends a Slack message and persists a Notification record in the DB."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_slack_cls = _make_slack_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    assert result["notification_sent"] is True

    notifications = await NotificationRepository().get_by_failure_id(db_session, failure.id)
    assert len(notifications) == 1
    assert notifications[0].channel == "slack"


async def test_notifier_slack_not_configured(db_session: AsyncSession):
    """When slack_bot_token is empty, notification_sent is False and post_message not called."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_slack_cls = _make_slack_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings(slack_bot_token="")

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    assert result["notification_sent"] is False
    mock_slack_cls.return_value.post_message.assert_not_called()


async def test_notifier_empty_failure_ids(db_session: AsyncSession):
    """When failure_ids is empty, notification_sent is False."""
    state = {**initial_state("test-event-id"), "failure_ids": []}

    mock_slack_cls = _make_slack_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    assert result["notification_sent"] is False


async def test_notifier_slack_exception(db_session: AsyncSession):
    """When post_message raises, the error is captured without re-raising."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_slack_instance = MagicMock()
    mock_slack_instance.post_message = AsyncMock(side_effect=Exception("timeout"))
    mock_slack_cls = MagicMock(return_value=mock_slack_instance)

    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    assert any("timeout" in err for err in result["errors"])
    assert result["notification_sent"] is False


async def test_notifier_includes_ticket_context(db_session: AsyncSession):
    """When ticket_url and ticket_id are in state, post_message is still called."""
    failure = await _make_failure(db_session)
    state = _build_state(
        failure,
        ticket_url="https://jira.example.com/browse/QA-1",
        ticket_id="QA-1",
    )

    mock_slack_cls = _make_slack_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    mock_slack_cls.return_value.post_message.assert_called_once()
    assert result["notification_sent"] is True


async def test_notifier_duplicate_still_sends(db_session: AsyncSession):
    """Notifier sends a Slack message even when is_duplicate=True."""
    failure = await _make_failure(db_session)
    state = _build_state(failure, is_duplicate=True)

    mock_slack_cls = _make_slack_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        result = await notifier_node(state)

    mock_slack_cls.return_value.post_message.assert_called_once()
    assert result["notification_sent"] is True


async def test_notifier_persists_external_message_id(db_session: AsyncSession):
    """The Notification record's external_message_id must match the Slack response 'ts'."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    expected_ts = "1234567890.123456"
    mock_slack_cls = _make_slack_mock(ts=expected_ts)
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.notifier.SlackClient", mock_slack_cls),
        patch(
            "src.agents.nodes.notifier.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.notifier.get_settings", return_value=mock_settings),
    ):
        await notifier_node(state)

    notifications = await NotificationRepository().get_by_failure_id(db_session, failure.id)
    assert len(notifications) == 1
    assert notifications[0].external_message_id == expected_ts
