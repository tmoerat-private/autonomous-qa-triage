from __future__ import annotations


def map_priority(category: str, confidence: float) -> str:
    """Map a FailureCategory string and confidence score to a Jira priority name.

    Rules are evaluated in first-match order:

    - ``"infra_issue"``                                  → ``"Critical"``
    - ``"product_bug"``   with confidence >= 0.8         → ``"High"``
    - ``"dependency_failure"`` with confidence >= 0.7    → ``"High"``
    - ``"flaky_test"``                                   → ``"Low"``
    - everything else                                    → ``"Medium"``

    Args:
        category: A ``FailureCategory`` value as a plain string, e.g.
            ``"product_bug"``.
        confidence: Classification confidence in the range ``[0.0, 1.0]``.

    Returns:
        A Jira priority name string suitable for the ``priority.name`` field.
    """
    if category == "infra_issue":
        return "Critical"
    if category == "product_bug" and confidence >= 0.8:
        return "High"
    if category == "dependency_failure" and confidence >= 0.7:
        return "High"
    if category == "flaky_test":
        return "Low"
    return "Medium"


def build_ticket_description(
    test_name: str,
    error_message: str | None,
    stack_trace: str | None,
    category: str,
    confidence: float,
    reasoning: str | None,
    repository: str | None,
    branch: str | None,
) -> str:
    """Build a structured plain-text Jira ticket description using wiki markup.

    Stack traces are capped at 3 000 characters to keep the ticket readable.

    Args:
        test_name: Fully-qualified test identifier.
        error_message: Short error message extracted from the failure.
        stack_trace: Full stack trace text; truncated to 3 000 chars if longer.
        category: ``FailureCategory`` string value.
        confidence: Classification confidence in ``[0.0, 1.0]``.
        reasoning: LLM reasoning narrative, or ``None``.
        repository: Repository name / full name, or ``None``.
        branch: Git branch name, or ``None``.

    Returns:
        A multi-line Jira wiki-markup string ready for the ``description``
        field of a ``create_issue`` call.
    """
    truncated_stack = stack_trace[:3000] if stack_trace else "N/A"

    return (
        f"h2. Test Failure Details\n"
        f"*Test:* {test_name}\n"
        f"*Repository:* {repository or 'N/A'}\n"
        f"*Branch:* {branch or 'N/A'}\n"
        f"\n"
        f"h2. Error\n"
        f"{{code}}\n"
        f"{error_message or 'N/A'}\n"
        f"{{code}}\n"
        f"\n"
        f"h2. Stack Trace\n"
        f"{{code}}\n"
        f"{truncated_stack}\n"
        f"{{code}}\n"
        f"\n"
        f"h2. AI Classification\n"
        f"*Category:* {category}\n"
        f"*Confidence:* {confidence:.0%}\n"
        f"*Reasoning:* {reasoning or 'N/A'}\n"
        f"\n"
        f"----\n"
        f"_Automatically triaged by Autonomous QA_"
    )
