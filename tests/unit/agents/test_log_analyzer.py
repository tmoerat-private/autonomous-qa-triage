"""Tests for normalize_error() and compute_signature() — pure functions, no DB, no async."""
from __future__ import annotations

import pytest

from src.agents.nodes.log_analyzer import compute_signature, normalize_error


# ---------------------------------------------------------------------------
# normalize_error — individual normalization steps
# ---------------------------------------------------------------------------


def test_ansi_codes_stripped():
    result = normalize_error("\x1b[31mERROR\x1b[0m")
    assert result == "ERROR"


def test_iso_timestamp_stripped():
    result = normalize_error("2024-01-15T10:30:00.123Z error")
    assert result == "error"


def test_time_timestamp_stripped():
    result = normalize_error("at 10:30:00 error")
    assert result == "at error"


def test_memory_address_stripped():
    result = normalize_error("at 0x7f8a1b2c error")
    assert result == "at error"


def test_line_number_stripped():
    result = normalize_error("File test.py, line 42")
    assert result == "File test.py,"


def test_uuid_stripped():
    result = normalize_error("id=550e8400-e29b-41d4-a716-446655440000 error")
    assert result == "id= error"


def test_multiple_normalizations():
    """ANSI codes, ISO timestamp, and UUID all stripped in a single pass."""
    raw = (
        "\x1b[31m2024-01-15T10:30:00Z\x1b[0m "
        "session=550e8400-e29b-41d4-a716-446655440000 failed"
    )
    result = normalize_error(raw)
    # No ANSI codes
    assert "\x1b" not in result
    # No ISO timestamp
    assert "2024-01-15T10:30:00Z" not in result
    # No UUID
    assert "550e8400-e29b-41d4-a716-446655440000" not in result
    # Meaningful words remain
    assert "session=" in result
    assert "failed" in result


def test_whitespace_collapsed():
    result = normalize_error("error  \n  message")
    assert result == "error message"


# ---------------------------------------------------------------------------
# compute_signature — hash properties
# ---------------------------------------------------------------------------


def test_empty_string_produces_hash():
    result = compute_signature("")
    assert isinstance(result, str)
    assert len(result) == 64


def test_compute_signature_deterministic():
    input_text = "AssertionError: expected True, got False"
    assert compute_signature(input_text) == compute_signature(input_text)


def test_compute_signature_different_inputs():
    hash1 = compute_signature("AssertionError: expected True, got False")
    hash2 = compute_signature("ConnectionError: timeout after 30s")
    assert hash1 != hash2


@pytest.mark.parametrize(
    "raw",
    [
        "simple error message",
        "\x1b[31mERROR\x1b[0m: something went wrong",
        "2024-01-15T10:30:00Z FAILED at 0x7fff1234 line 99",
        "session=550e8400-e29b-41d4-a716-446655440000 crashed",
        "",
    ],
)
def test_signature_length_always_64(raw: str):
    result = compute_signature(raw)
    assert len(result) == 64
