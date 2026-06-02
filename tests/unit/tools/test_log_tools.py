"""Tests for log_tools.py — pure function tests, no mocks needed."""
from __future__ import annotations

import pytest

from src.agents.tools.log_tools import (
    classify_error_type,
    extract_stack_frames,
    extract_test_names_from_log,
    normalize_error_signature,
)

# ---------------------------------------------------------------------------
# Fixtures / shared data
# ---------------------------------------------------------------------------

PYTHON_TRACEBACK = """\
Traceback (most recent call last):
  File "src/checkout.py", line 42, in process_payment
    result = gateway.charge(amount)
  File "src/gateway.py", line 17, in charge
    return self._post("/charge", data)
AssertionError: expected 99.99 but got 0.00
"""

MULTI_FAILURE_LOG = """\
collecting ... collected 5 items

FAILED tests/test_checkout.py::test_payment_declined - AssertionError: expected True
FAILED tests/test_auth.py::test_login_timeout - TimeoutError: timed out after 5s
FAILED tests/test_db.py::TestSession::test_connect - ConnectionError: refused
PASSED tests/test_health.py::test_ping
"""

DUPLICATE_FAILURE_LOG = """\
FAILED tests/test_checkout.py::test_payment_declined - AssertionError: expected True
PASSED tests/test_health.py::test_ping
FAILED tests/test_checkout.py::test_payment_declined - AssertionError: expected True
"""

# ---------------------------------------------------------------------------
# normalize_error_signature tests
# ---------------------------------------------------------------------------


def test_normalize_error_signature_is_deterministic():
    """Same input produces the same SHA-256 digest on two calls."""
    raw = "AssertionError: expected 99.99 but got 0.00 at line 42"
    result_1 = normalize_error_signature.invoke({"raw_error": raw})
    result_2 = normalize_error_signature.invoke({"raw_error": raw})
    assert result_1 == result_2
    assert len(result_1) == 64  # SHA-256 hex is always 64 chars


def test_normalize_error_signature_strips_ansi():
    """Input with ANSI codes produces same digest as the plain version."""
    plain = "ERROR: connection refused"
    ansi = "\x1b[31mERROR\x1b[0m: connection refused"
    assert normalize_error_signature.invoke({"raw_error": plain}) == normalize_error_signature.invoke(
        {"raw_error": ansi}
    )


def test_normalize_error_signature_strips_uuid():
    """Two errors differing only by UUID map to the same digest."""
    base = "Session {} failed with unexpected error"
    err_a = base.format("550e8400-e29b-41d4-a716-446655440000")
    err_b = base.format("f47ac10b-58cc-4372-a567-0e02b2c3d479")
    assert normalize_error_signature.invoke({"raw_error": err_a}) == normalize_error_signature.invoke(
        {"raw_error": err_b}
    )


def test_normalize_error_signature_strips_memory_address():
    """Two errors differing only by memory address produce the same digest."""
    err_a = "Segfault at 0x7f1234abcd: null pointer dereference"
    err_b = "Segfault at 0xdeadbeef12: null pointer dereference"
    assert normalize_error_signature.invoke({"raw_error": err_a}) == normalize_error_signature.invoke(
        {"raw_error": err_b}
    )


def test_normalize_error_signature_returns_hex_string():
    """Return value is a 64-char lowercase hex string."""
    result = normalize_error_signature.invoke({"raw_error": "some error"})
    assert isinstance(result, str)
    assert len(result) == 64
    assert all(c in "0123456789abcdef" for c in result)


# ---------------------------------------------------------------------------
# extract_stack_frames tests
# ---------------------------------------------------------------------------


def test_extract_stack_frames_python_format():
    """Parse a realistic Python traceback into structured frames."""
    frames = extract_stack_frames.invoke({"stack_trace": PYTHON_TRACEBACK})

    assert len(frames) == 2

    first = frames[0]
    assert first["file"] == "src/checkout.py"
    assert first["line"] == 42
    assert first["function"] == "process_payment"
    assert first["code"] == "result = gateway.charge(amount)"

    second = frames[1]
    assert second["file"] == "src/gateway.py"
    assert second["line"] == 17
    assert second["function"] == "charge"
    assert second["code"] == "return self._post(\"/charge\", data)"


def test_extract_stack_frames_empty():
    """Empty input returns an empty list."""
    frames = extract_stack_frames.invoke({"stack_trace": ""})
    assert frames == []


def test_extract_stack_frames_no_frames():
    """Log text with no frame lines returns an empty list."""
    frames = extract_stack_frames.invoke(
        {"stack_trace": "AssertionError: expected True but got False\n"}
    )
    assert frames == []


def test_extract_stack_frames_at_format():
    """Parse compact 'at file:line' format."""
    trace = "  at src/checkout.py:42\n  at src/gateway.py:17\n"
    frames = extract_stack_frames.invoke({"stack_trace": trace})

    assert len(frames) == 2
    assert frames[0]["file"] == "src/checkout.py"
    assert frames[0]["line"] == 42
    assert frames[0]["function"] is None
    assert frames[0]["code"] is None


# ---------------------------------------------------------------------------
# classify_error_type tests — parametrized across all 5 categories
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message,expected",
    [
        ("AssertionError: expected True but got False", "assertion_failure"),
        ("ASSERTIONERROR: value mismatch", "assertion_failure"),
        ("TimeoutError: operation timed out after 30s", "timeout"),
        ("request timed out connecting to database", "timeout"),
        ("ConnectionError: connection refused on port 5432", "network_error"),
        ("connect: connection refused", "network_error"),
        ("ImportError: No module named 'boto3'", "import_error"),
        ("ModuleNotFoundError: No module named 'requests'", "import_error"),
        ("RuntimeError: unexpected server response 503", "unknown"),
        ("", "unknown"),
    ],
)
def test_classify_error_type_parametrized(message: str, expected: str):
    result = classify_error_type.invoke({"error_message": message})
    assert result == expected


# ---------------------------------------------------------------------------
# extract_test_names_from_log tests
# ---------------------------------------------------------------------------


def test_extract_test_names_from_log():
    """Parse a multi-line pytest log with 3 FAILED lines and assert names list."""
    names = extract_test_names_from_log.invoke({"log_text": MULTI_FAILURE_LOG})

    assert len(names) == 3
    assert "tests/test_checkout.py::test_payment_declined" in names
    assert "tests/test_auth.py::test_login_timeout" in names
    assert "tests/test_db.py::TestSession::test_connect" in names


def test_extract_test_names_deduplicates():
    """Same test appearing twice in the log appears only once in the output."""
    names = extract_test_names_from_log.invoke({"log_text": DUPLICATE_FAILURE_LOG})
    assert names.count("tests/test_checkout.py::test_payment_declined") == 1
    assert len(names) == 1


def test_extract_test_names_empty_log():
    """Empty input returns an empty list."""
    names = extract_test_names_from_log.invoke({"log_text": ""})
    assert names == []


def test_extract_test_names_no_failures():
    """Log with only PASSED lines returns an empty list."""
    log = "PASSED tests/test_health.py::test_ping\nPASSED tests/test_ready.py::test_ok\n"
    names = extract_test_names_from_log.invoke({"log_text": log})
    assert names == []


def test_extract_test_names_with_reason():
    """Test names are extracted correctly when ' - reason' suffix is present."""
    log = "FAILED tests/test_foo.py::test_bar - AssertionError: boom\n"
    names = extract_test_names_from_log.invoke({"log_text": log})
    assert names == ["tests/test_foo.py::test_bar"]
