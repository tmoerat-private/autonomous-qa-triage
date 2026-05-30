from __future__ import annotations

TICKET_SUMMARY_TEMPLATE: str = "Test Failure: {test_name} [{category}]"


def format_ticket_summary(test_name: str, category: str) -> str:
    """Format a one-line Jira ticket summary from the test name and category.

    Args:
        test_name: Fully-qualified test identifier, e.g. ``"tests.auth.test_login"``.
        category: ``FailureCategory`` string value, e.g. ``"product_bug"``.

    Returns:
        A short summary string suitable for the Jira ``summary`` field.
    """
    return TICKET_SUMMARY_TEMPLATE.format(test_name=test_name, category=category)
