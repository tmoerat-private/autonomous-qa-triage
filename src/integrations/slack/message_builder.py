from __future__ import annotations

CATEGORY_EMOJI: dict[str, str] = {
    "product_bug": "🐛",
    "flaky_test": "🎲",
    "env_issue": "🔧",
    "timeout": "⏱️",
    "infra_issue": "🏗️",
    "config_error": "⚙️",
    "dependency_failure": "📦",
}


def build_triage_notification(
    test_name: str,
    category: str,
    confidence: float,
    reasoning: str | None,
    repository: str | None,
    branch: str | None,
    ticket_url: str | None,
    ticket_key: str | None,
    is_duplicate: bool,
) -> dict:
    """Build a Slack Block Kit payload for a triage result notification.

    Returns a dict with two keys:
    - ``"blocks"``: list of Block Kit block dicts.
    - ``"text"``: plain-text fallback string for clients that cannot render blocks.

    Args:
        test_name: Fully-qualified test identifier.
        category: ``FailureCategory`` string value, e.g. ``"product_bug"``.
        confidence: Classification confidence in ``[0.0, 1.0]``.
        reasoning: LLM reasoning narrative, or ``None``.
        repository: Repository name / full name, or ``None``.
        branch: Git branch name, or ``None``.
        ticket_url: URL to the created Jira ticket, or ``None`` if no ticket.
        ticket_key: Jira issue key (e.g. ``"QA-42"``), or ``None``.
        is_duplicate: Whether this failure matches an existing known signature.

    Returns:
        ``{"blocks": list[dict], "text": str}``
    """
    emoji = CATEGORY_EMOJI.get(category, "❓")

    if is_duplicate:
        header_text = f"🔁 Duplicate Failure Detected: {test_name}"
    else:
        header_text = f"{emoji} Test Failure Triaged: {category}"

    blocks: list[dict] = []

    # 1. Header block
    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": header_text, "emoji": True},
    })

    # 2. Fields section
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Test:*\n{test_name}"},
            {"type": "mrkdwn", "text": f"*Repository:*\n{repository or 'N/A'}"},
            {"type": "mrkdwn", "text": f"*Branch:*\n{branch or 'N/A'}"},
            {"type": "mrkdwn", "text": f"*Confidence:*\n{confidence:.0%}"},
        ],
    })

    # 3. Reasoning section (only when present)
    if reasoning:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Reasoning:*\n{reasoning}"},
        })

    # 4. Actions block (only when a Jira ticket was created)
    if ticket_url:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {
                    "type": "plain_text",
                    "text": f"View Jira Ticket {ticket_key or ''}",
                    "emoji": True,
                },
                "url": ticket_url,
                "action_id": "view_ticket",
            }],
        })

    # 5. Divider
    blocks.append({"type": "divider"})

    text = f"Test failure triaged: {test_name} → {category}"
    return {"blocks": blocks, "text": text}
