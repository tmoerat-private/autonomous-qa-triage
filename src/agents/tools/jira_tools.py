"""LangChain tools for Jira API operations.

Each tool wraps the JiraClient integration, handles errors gracefully, and
returns typed dicts so agent nodes never have to parse free-text or catch
exceptions from downstream HTTP calls.

Tool list
---------
- create_jira_ticket        Create a new Bug issue; returns ticket_id and url.
- link_duplicate_ticket     Create an "is duplicate of" issue link.
- get_ticket_status         Fetch status, assignee, and resolution for a ticket.
- search_similar_tickets    JQL text search for similar open issues.
"""

from __future__ import annotations

import structlog
from langchain_core.tools import tool

from src.config.settings import get_settings
from src.integrations.jira.client import JiraClient

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool 1: create_jira_ticket
# ---------------------------------------------------------------------------


@tool
async def create_jira_ticket(
    title: str,
    description: str,
    priority: str,
    labels: list[str],
) -> dict:
    """Create a Jira Bug issue and return its identifier and browse URL.

    This tool is the authoritative way for agent nodes to open a new Jira
    ticket.  It delegates all HTTP logic — including authentication, retries,
    and ADF body serialisation — to the underlying ``JiraClient``.

    Args:
        title: One-line issue summary shown in the Jira issue list.
        description: Plain-text body for the ticket.  The client will wrap
            this in the Jira Atlassian Document Format (ADF) paragraph node
            automatically.
        priority: Jira priority name.  Accepted values: ``"Critical"``,
            ``"High"``, ``"Medium"``, ``"Low"``.  Use ``mapper.map_priority``
            to derive this value from a classification result before calling
            this tool.
        labels: List of Jira label strings to apply to the issue.  An empty
            list causes the client to fall back to
            ``["autonomous-qa", "test-failure"]``.

    Returns:
        On success::

            {"ticket_id": str, "url": str}

        On failure::

            {"ticket_id": None, "url": None, "error": str}
    """
    settings = get_settings()

    try:
        async with JiraClient(settings) as client:
            result = await client.create_issue(
                summary=title,
                description=description,
                priority=priority,
                labels=labels or [],
            )
    except Exception as exc:
        logger.error(
            "jira_tools.create_jira_ticket.failed",
            title=title,
            priority=priority,
            error=str(exc),
        )
        return {"ticket_id": None, "url": None, "error": str(exc)}

    logger.info(
        "jira_tools.create_jira_ticket.success",
        ticket_id=result["key"],
        url=result["url"],
    )
    return {"ticket_id": result["key"], "url": result["url"]}


# ---------------------------------------------------------------------------
# Tool 2: link_duplicate_ticket
# ---------------------------------------------------------------------------


@tool
async def link_duplicate_ticket(
    source_ticket_id: str,
    duplicate_ticket_id: str,
) -> bool:
    """Create an "is duplicate of" issue link between two Jira tickets.

    The link is directional: ``source_ticket_id`` *is duplicate of*
    ``duplicate_ticket_id``.  This is the standard Jira duplicate relationship
    and will appear on both issues' "Issue Links" panels.

    The call is made against Jira REST API v3 ``POST /rest/api/3/issueLink``.
    The link type name ``"Duplicate"`` is standard on all Jira Cloud instances;
    for custom Jira Server setups the administrator may need to confirm the
    exact link type name.

    Args:
        source_ticket_id: The Jira issue key for the *new* (duplicate) ticket,
            e.g. ``"QA-99"``.
        duplicate_ticket_id: The Jira issue key for the *original* ticket that
            this failure duplicates, e.g. ``"QA-42"``.

    Returns:
        ``True`` if the link was created successfully, ``False`` otherwise.
    """
    settings = get_settings()

    payload = {
        "type": {"name": "Duplicate"},
        "inwardIssue": {"key": duplicate_ticket_id},
        "outwardIssue": {"key": source_ticket_id},
    }

    try:
        async with JiraClient(settings) as client:
            response = await client.client.post(
                "/rest/api/3/issueLink",
                json=payload,
            )
            # 201 Created on success; raise on anything else
            response.raise_for_status()
    except Exception as exc:
        logger.error(
            "jira_tools.link_duplicate_ticket.failed",
            source=source_ticket_id,
            duplicate_of=duplicate_ticket_id,
            error=str(exc),
        )
        return False

    logger.info(
        "jira_tools.link_duplicate_ticket.success",
        source=source_ticket_id,
        duplicate_of=duplicate_ticket_id,
    )
    return True


# ---------------------------------------------------------------------------
# Tool 3: get_ticket_status
# ---------------------------------------------------------------------------


@tool
async def get_ticket_status(ticket_id: str) -> dict:
    """Fetch the current status, assignee, and resolution for a Jira ticket.

    Uses ``JiraClient.get_issue`` internally.  The method fetches the full
    issue JSON and extracts the three fields the triage agent cares about.
    All three values are normalised to strings — missing values become
    ``"Unassigned"`` or ``"None"`` rather than ``None`` so downstream agents
    do not need to guard against null checks.

    Args:
        ticket_id: The Jira issue key, e.g. ``"QA-42"``.

    Returns:
        On success::

            {"status": str, "assignee": str, "resolution": str}

        On failure::

            {"status": "unknown", "assignee": "unknown", "resolution": "unknown",
             "error": str}
    """
    settings = get_settings()

    try:
        async with JiraClient(settings) as client:
            issue = await client.get_issue(ticket_id)
    except Exception as exc:
        logger.error(
            "jira_tools.get_ticket_status.failed",
            ticket_id=ticket_id,
            error=str(exc),
        )
        return {
            "status": "unknown",
            "assignee": "unknown",
            "resolution": "unknown",
            "error": str(exc),
        }

    fields = issue.get("fields", {})

    status: str = (
        fields.get("status", {}).get("name", "Unknown") or "Unknown"
    )

    assignee_obj = fields.get("assignee") or {}
    assignee: str = (
        assignee_obj.get("displayName")
        or assignee_obj.get("emailAddress")
        or "Unassigned"
    )

    resolution_obj = fields.get("resolution") or {}
    resolution: str = resolution_obj.get("name", "None") or "None"

    logger.info(
        "jira_tools.get_ticket_status.success",
        ticket_id=ticket_id,
        status=status,
        assignee=assignee,
        resolution=resolution,
    )
    return {"status": status, "assignee": assignee, "resolution": resolution}


# ---------------------------------------------------------------------------
# Tool 4: search_similar_tickets
# ---------------------------------------------------------------------------


@tool
async def search_similar_tickets(
    error_signature: str,
    project_key: str,
    limit: int = 5,
) -> list[dict]:
    """Search Jira for issues that contain the given error signature text.

    Executes a JQL ``text ~ "<error_signature>"`` query scoped to the
    supplied ``project_key``.  Results are ordered by creation date
    descending so the most recent similar failures appear first.

    The search deliberately targets the full-text index (``text ~``) rather
    than an exact ``summary =`` match because error signatures may appear
    anywhere in the issue body, comments, or summary.

    Jira REST API v3: ``GET /rest/api/3/search``

    Args:
        error_signature: Normalised error signature string (output of
            ``generate_signature`` in the log analyser node) or any
            representative substring of the error message to search for.
        project_key: The Jira project key to scope the search, e.g. ``"QA"``.
            Defaults to the value from settings if the caller passes an empty
            string.
        limit: Maximum number of results to return.  Capped at 20 to avoid
            oversized agent context windows.  Defaults to 5.

    Returns:
        A list of dicts (possibly empty on no matches or failure)::

            [
                {
                    "id":     str,   # Jira issue key, e.g. "QA-42"
                    "title":  str,   # Issue summary line
                    "status": str,   # Status name, e.g. "Open"
                    "url":    str,   # Full browse URL
                },
                ...
            ]

        Returns an empty list on error rather than raising.
    """
    settings = get_settings()

    effective_project = project_key or settings.jira_project_key
    capped_limit = min(limit, 20)

    # Escape double-quotes in the signature before embedding in JQL
    safe_signature = error_signature.replace('"', '\\"')
    jql = (
        f'project = "{effective_project}" '
        f'AND text ~ "{safe_signature}" '
        f"ORDER BY created DESC"
    )

    params: dict[str, str | int] = {
        "jql": jql,
        "maxResults": capped_limit,
        "fields": "summary,status",
    }

    try:
        async with JiraClient(settings) as client:
            response = await client.client.get(
                "/rest/api/3/search",
                params=params,
            )
            response.raise_for_status()
            data = response.json()
    except Exception as exc:
        logger.error(
            "jira_tools.search_similar_tickets.failed",
            project_key=effective_project,
            error=str(exc),
        )
        return []

    issues: list[dict] = []
    for issue in data.get("issues", []):
        key = issue.get("key", "")
        fields = issue.get("fields", {})
        issues.append(
            {
                "id": key,
                "title": fields.get("summary", ""),
                "status": fields.get("status", {}).get("name", "Unknown"),
                "url": f"{settings.jira_url}/browse/{key}",
            }
        )

    logger.info(
        "jira_tools.search_similar_tickets.success",
        project_key=effective_project,
        result_count=len(issues),
    )
    return issues
