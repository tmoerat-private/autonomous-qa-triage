from __future__ import annotations

import re

_LABEL_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def slugify_label(text: str, max_length: int = 50) -> str:
    """Convert arbitrary text into a Jira-safe label.

    Jira labels cannot contain whitespace and reject several punctuation
    characters. Test names — especially Playwright-style names such as
    ``"[chromium] dark mode toggle"`` — routinely contain spaces, brackets,
    and other characters that Jira's ``POST /rest/api/3/issue`` endpoint
    rejects with a 400 (``"The label '...' can't contain spaces."``).

    This collapses any run of characters outside ``[A-Za-z0-9_-]`` into a
    single hyphen, trims leading/trailing hyphens, and truncates to
    ``max_length`` characters.

    Args:
        text: Arbitrary text to convert into a label, e.g. a test name.
        max_length: Maximum length of the resulting label. Defaults to 50.

    Returns:
        A non-empty, Jira-safe label string. Falls back to ``"test-failure"``
        if the input contains no valid characters.
    """
    slug = _LABEL_INVALID_CHARS.sub("-", text.strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    slug = slug[:max_length].strip("-")
    return slug or "test-failure"


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
