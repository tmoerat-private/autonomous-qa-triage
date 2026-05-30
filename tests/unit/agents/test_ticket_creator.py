"""Tests for ticket_creator_node() — mocked JiraClient, real test DB."""
from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from sqlalchemy.ext.asyncio import AsyncSession

from src.agents.nodes.ticket_creator import ticket_creator_node
from src.agents.state import initial_state
from src.db.repositories.ticket_repo import TicketRepository
from src.models.failure_classification import FailureClassification
from src.models.pipeline_event import PipelineEvent
from src.models.test_failure import TestFailure

# ---------------------------------------------------------------------------
# Helpers (mirror pattern from test_failure_classifier.py)
# ---------------------------------------------------------------------------


async def _make_failure(db_session: AsyncSession) -> TestFailure:
    """Insert a PipelineEvent + TestFailure into the test DB and return the failure."""
    event = PipelineEvent(
        provider="jenkins",
        provider_build_id="build-1",
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
        test_name="test_checkout_total",
        error_message="AssertionError: expected 99.99 but got 0.00",
        stack_trace="File checkout.py, line 88\nAssertionError",
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


def _make_jira_mock(
    issue_key: str = "QA-1",
    issue_id: str = "10001",
    base_url: str = "https://jira.example.com",
) -> MagicMock:
    """Return a JiraClient class mock that works as an async context manager."""
    mock_jira_instance = MagicMock()
    mock_jira_instance.create_issue = AsyncMock(
        return_value={
            "id": issue_id,
            "key": issue_key,
            "url": f"{base_url}/browse/{issue_key}",
        }
    )
    mock_jira_instance.__aenter__ = AsyncMock(return_value=mock_jira_instance)
    mock_jira_instance.__aexit__ = AsyncMock(return_value=False)
    mock_jira_cls = MagicMock(return_value=mock_jira_instance)
    return mock_jira_cls


def _make_settings(
    jira_url: str = "https://jira.example.com",
    jira_project_key: str = "QA",
    jira_email: str = "test@example.com",
    jira_api_token: str = "token",
) -> MagicMock:
    s = MagicMock()
    s.jira_url = jira_url
    s.jira_project_key = jira_project_key
    s.jira_email = jira_email
    s.jira_api_token = jira_api_token
    s.slack_bot_token = ""
    s.slack_channel_id = ""
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


async def test_ticket_creator_happy_path(db_session: AsyncSession):
    """Node creates a Jira ticket and persists a TriageTicket record in the DB."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_jira_cls = _make_jira_mock(issue_key="QA-1")
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    assert result["ticket_id"] == "QA-1"
    assert result["ticket_url"] is not None
    assert "QA-1" in result["ticket_url"]

    # Verify TriageTicket persisted in DB
    ticket = await TicketRepository().get_by_failure_id(db_session, failure.id)
    assert ticket is not None
    assert ticket.external_ticket_id == "QA-1"
    assert ticket.provider == "jira"


async def test_ticket_creator_skips_duplicates(db_session: AsyncSession):
    """When is_duplicate=True, no Jira issue is created and ticket_id is None."""
    failure = await _make_failure(db_session)
    state = _build_state(failure, is_duplicate=True)

    mock_jira_cls = _make_jira_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    assert result["ticket_id"] is None
    assert result["ticket_url"] is None
    mock_jira_cls.return_value.create_issue.assert_not_called()


async def test_ticket_creator_jira_not_configured(db_session: AsyncSession):
    """When jira_url is empty, no Jira call is made and ticket_id is None."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_jira_cls = _make_jira_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings(jira_url="")

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    assert result["ticket_id"] is None
    mock_jira_cls.return_value.create_issue.assert_not_called()


async def test_ticket_creator_empty_failure_ids(db_session: AsyncSession):
    """When failure_ids is empty, the node returns ticket_id=None immediately."""
    state = {**initial_state("test-event-id"), "failure_ids": []}

    mock_jira_cls = _make_jira_mock()
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    assert result["ticket_id"] is None
    assert result["ticket_url"] is None


async def test_ticket_creator_jira_exception(db_session: AsyncSession):
    """When create_issue raises, the error is captured and ticket_id is None."""
    failure = await _make_failure(db_session)
    state = _build_state(failure)

    mock_jira_instance = MagicMock()
    mock_jira_instance.create_issue = AsyncMock(side_effect=Exception("connection refused"))
    mock_jira_instance.__aenter__ = AsyncMock(return_value=mock_jira_instance)
    mock_jira_instance.__aexit__ = AsyncMock(return_value=False)
    mock_jira_cls = MagicMock(return_value=mock_jira_instance)

    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    assert result["ticket_id"] is None
    assert any("connection refused" in err for err in result["errors"])


async def test_ticket_creator_no_classification(db_session: AsyncSession):
    """When no FailureClassification exists, the node still calls create_issue with defaults."""
    failure = await _make_failure(db_session)
    # No classification inserted — ClassificationRepository.get_by_failure_id returns None
    state = _build_state(failure)

    mock_jira_cls = _make_jira_mock(issue_key="QA-99")
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        result = await ticket_creator_node(state)

    # Should have still created a ticket using default category/confidence
    mock_jira_cls.return_value.create_issue.assert_called_once()
    assert result["ticket_id"] == "QA-99"


async def test_ticket_creator_sets_correct_priority(db_session: AsyncSession):
    """When classification is infra_issue, create_issue is called with priority='Critical'."""
    failure = await _make_failure(db_session)

    # Insert a FailureClassification with infra_issue category
    classification = FailureClassification(
        test_failure_id=failure.id,
        category="infra_issue",
        confidence=0.9,
        reasoning="Infrastructure problem detected",
    )
    db_session.add(classification)
    await db_session.flush()

    state = _build_state(failure)

    mock_jira_cls = _make_jira_mock(issue_key="QA-7")
    session_factory = _make_session_factory(db_session)
    mock_settings = _make_settings()

    with (
        patch("src.agents.nodes.ticket_creator.JiraClient", mock_jira_cls),
        patch(
            "src.agents.nodes.ticket_creator.get_session_factory",
            return_value=session_factory,
        ),
        patch("src.agents.nodes.ticket_creator.get_settings", return_value=mock_settings),
    ):
        await ticket_creator_node(state)

    call_kwargs = mock_jira_cls.return_value.create_issue.call_args
    assert call_kwargs.kwargs["priority"] == "Critical"
