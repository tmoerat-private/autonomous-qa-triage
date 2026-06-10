"""Tests for map_priority(), build_ticket_description(), and slugify_label() —
pure functions, no async."""
from __future__ import annotations

import pytest

from src.integrations.jira.mapper import build_ticket_description, map_priority, slugify_label

# ---------------------------------------------------------------------------
# map_priority() tests
# ---------------------------------------------------------------------------

VALID_PRIORITIES = {"Critical", "High", "Medium", "Low"}


def test_infra_issue_is_critical():
    assert map_priority("infra_issue", 0.5) == "Critical"


def test_infra_issue_any_confidence_is_critical():
    assert map_priority("infra_issue", 0.1) == "Critical"


def test_product_bug_high_confidence_is_high():
    assert map_priority("product_bug", 0.9) == "High"


def test_product_bug_low_confidence_is_medium():
    assert map_priority("product_bug", 0.5) == "Medium"


def test_product_bug_boundary_confidence():
    """Exact boundary 0.8 should be High (confidence >= 0.8)."""
    assert map_priority("product_bug", 0.8) == "High"


def test_dependency_failure_high_confidence_is_high():
    assert map_priority("dependency_failure", 0.8) == "High"


def test_dependency_failure_low_confidence_is_medium():
    assert map_priority("dependency_failure", 0.5) == "Medium"


def test_flaky_test_is_low():
    assert map_priority("flaky_test", 0.9) == "Low"


def test_config_error_is_medium():
    assert map_priority("config_error", 0.9) == "Medium"


def test_timeout_is_medium():
    assert map_priority("timeout", 0.9) == "Medium"


def test_env_issue_is_medium():
    assert map_priority("env_issue", 0.9) == "Medium"


@pytest.mark.parametrize(
    "category,confidence",
    [
        ("infra_issue", 0.5),
        ("product_bug", 0.9),
        ("dependency_failure", 0.8),
        ("flaky_test", 0.9),
        ("config_error", 0.9),
        ("timeout", 0.9),
        ("env_issue", 0.9),
    ],
)
def test_all_categories_return_valid_priority(category: str, confidence: float):
    """Every FailureCategory must produce a priority in the approved set."""
    result = map_priority(category, confidence)
    assert result in VALID_PRIORITIES


# ---------------------------------------------------------------------------
# build_ticket_description() tests
# ---------------------------------------------------------------------------


def _default_description(**overrides) -> str:
    """Call build_ticket_description() with sensible defaults and optional overrides."""
    kwargs = dict(
        test_name="tests.auth.test_login",
        error_message="AssertionError: expected 200, got 500",
        stack_trace="Traceback (most recent call last):\n  File test.py, line 42\nAssertionError",
        category="product_bug",
        confidence=0.85,
        reasoning="Assertion error in business logic",
        repository="org/repo",
        branch="main",
    )
    kwargs.update(overrides)
    return build_ticket_description(**kwargs)


def test_description_contains_test_name():
    result = _default_description(test_name="tests.checkout.test_total")
    assert "tests.checkout.test_total" in result


def test_description_contains_error_message():
    result = _default_description(error_message="ValueError: negative price")
    assert "ValueError: negative price" in result


def test_description_contains_category():
    result = _default_description(category="infra_issue")
    assert "infra_issue" in result


def test_description_contains_confidence_percentage():
    """Confidence 0.85 must appear as '85%' in the output."""
    result = _default_description(confidence=0.85)
    assert "85%" in result


def test_description_handles_none_values():
    """build_ticket_description() must not raise when optional args are None."""
    result = build_ticket_description(
        test_name="tests.foo.test_bar",
        error_message=None,
        stack_trace=None,
        category="timeout",
        confidence=0.6,
        reasoning=None,
        repository=None,
        branch=None,
    )
    # Should complete and produce a non-empty string
    assert isinstance(result, str)
    assert len(result) > 0


def test_long_stack_trace_truncated():
    """A 10 000-character stack trace must not appear in full in the description."""
    long_trace = "x" * 10_000
    result = _default_description(stack_trace=long_trace)
    # The full 10 000-char trace must not be present
    assert long_trace not in result
    # The description must still contain some truncated portion
    assert "x" * 3000 in result
    assert "x" * 3001 not in result


# ---------------------------------------------------------------------------
# slugify_label() tests
# ---------------------------------------------------------------------------


def test_slugify_label_replaces_spaces_with_hyphens():
    assert slugify_label("dark mode toggle") == "dark-mode-toggle"


def test_slugify_label_strips_brackets_and_punctuation():
    """Playwright-style names like '[chromium] dark mode toggle' must not
    leave brackets or other invalid characters in the label."""
    result = slugify_label("[chromium] dark mode toggle")
    assert result == "chromium-dark-mode-toggle"
    for char in result:
        assert char.isalnum() or char in {"-", "_"}


def test_slugify_label_collapses_consecutive_separators():
    """Multiple adjacent invalid characters collapse into a single hyphen."""
    result = slugify_label("Sidebar navigation › dark mode toggle")  # noqa: RUF001
    assert "--" not in result
    assert result == "Sidebar-navigation-dark-mode-toggle"


def test_slugify_label_strips_leading_and_trailing_hyphens():
    result = slugify_label("  /weird/ path/ ")
    assert not result.startswith("-")
    assert not result.endswith("-")


def test_slugify_label_truncates_to_max_length():
    text = "a" * 100
    result = slugify_label(text)
    assert len(result) == 50


def test_slugify_label_respects_custom_max_length():
    result = slugify_label("a-very-long-test-name-here", max_length=10)
    assert len(result) <= 10


def test_slugify_label_truncation_does_not_leave_trailing_hyphen():
    """If truncation lands mid-separator-run, the trailing hyphen is stripped."""
    result = slugify_label("a" * 49 + " " + "b" * 10, max_length=50)
    assert not result.endswith("-")


def test_slugify_label_preserves_existing_hyphens_and_underscores():
    assert slugify_label("test_name-with_mixed-separators") == "test_name-with_mixed-separators"


def test_slugify_label_empty_input_falls_back_to_default():
    assert slugify_label("") == "test-failure"


def test_slugify_label_only_invalid_chars_falls_back_to_default():
    assert slugify_label("///   ›››") == "test-failure"  # noqa: RUF001
