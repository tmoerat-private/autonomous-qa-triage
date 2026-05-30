"""Tests for build_triage_notification() and CATEGORY_EMOJI — pure functions, no async."""
from __future__ import annotations

import pytest

from src.integrations.slack.message_builder import CATEGORY_EMOJI, build_triage_notification

# All seven FailureCategory values as plain strings (mirrors the enum)
_ALL_CATEGORIES = [
    "product_bug",
    "flaky_test",
    "env_issue",
    "timeout",
    "infra_issue",
    "config_error",
    "dependency_failure",
]


def _default_payload(**overrides) -> dict:
    """Return build_triage_notification() output with sensible defaults."""
    kwargs = dict(
        test_name="tests.auth.test_login",
        category="product_bug",
        confidence=0.85,
        reasoning="Assertion error in business logic",
        repository="org/repo",
        branch="main",
        ticket_url=None,
        ticket_key=None,
        is_duplicate=False,
    )
    kwargs.update(overrides)
    return build_triage_notification(**kwargs)


# ---------------------------------------------------------------------------
# Structure tests
# ---------------------------------------------------------------------------


def test_returns_blocks_and_text_keys():
    result = _default_payload()
    assert "blocks" in result
    assert "text" in result


def test_blocks_is_nonempty_list():
    result = _default_payload()
    assert isinstance(result["blocks"], list)
    assert len(result["blocks"]) > 0


def test_text_contains_test_name():
    result = _default_payload(test_name="tests.checkout.test_total")
    assert "tests.checkout.test_total" in result["text"]


# ---------------------------------------------------------------------------
# Header content tests
# ---------------------------------------------------------------------------


def test_duplicate_header_says_duplicate():
    """When is_duplicate=True the header block text must contain 'Duplicate'."""
    result = _default_payload(is_duplicate=True)
    header_block = result["blocks"][0]
    assert header_block["type"] == "header"
    assert "Duplicate" in header_block["text"]["text"]


def test_non_duplicate_header_contains_category():
    """When is_duplicate=False the header block text must contain the category."""
    result = _default_payload(is_duplicate=False, category="infra_issue")
    header_block = result["blocks"][0]
    assert header_block["type"] == "header"
    assert "infra_issue" in header_block["text"]["text"]


# ---------------------------------------------------------------------------
# Actions block tests
# ---------------------------------------------------------------------------


def test_ticket_url_adds_actions_block():
    """When ticket_url is provided, at least one block must have type=='actions'."""
    result = _default_payload(
        ticket_url="https://jira.example.com/browse/QA-1",
        ticket_key="QA-1",
    )
    block_types = [b["type"] for b in result["blocks"]]
    assert "actions" in block_types


def test_no_ticket_url_no_actions_block():
    """When ticket_url is None, no block should have type=='actions'."""
    result = _default_payload(ticket_url=None)
    block_types = [b["type"] for b in result["blocks"]]
    assert "actions" not in block_types


# ---------------------------------------------------------------------------
# Parametrized category tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("category", _ALL_CATEGORIES)
def test_all_categories_produce_valid_payload(category: str):
    """Every FailureCategory value must produce a result with 'blocks' and 'text'."""
    result = build_triage_notification(
        test_name="tests.foo.test_bar",
        category=category,
        confidence=0.75,
        reasoning="Some reasoning",
        repository="org/repo",
        branch="main",
        ticket_url=None,
        ticket_key=None,
        is_duplicate=False,
    )
    assert "blocks" in result
    assert isinstance(result["blocks"], list)
    assert len(result["blocks"]) > 0
    assert "text" in result
    assert isinstance(result["text"], str)


# ---------------------------------------------------------------------------
# CATEGORY_EMOJI tests
# ---------------------------------------------------------------------------


def test_category_emoji_has_all_seven():
    """CATEGORY_EMOJI must contain exactly one entry for each of the 7 categories."""
    assert len(CATEGORY_EMOJI) == 7
    for category in _ALL_CATEGORIES:
        assert category in CATEGORY_EMOJI, f"Missing emoji for category: {category}"
