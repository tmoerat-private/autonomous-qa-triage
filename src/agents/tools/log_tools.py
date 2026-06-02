"""LangChain tool functions for log analysis and error signature generation.

These tools are pure functions — no I/O, no database calls, no side effects.
They are designed to be called by Claude-powered agent nodes during the triage
pipeline, specifically the log_analyzer node.

Normalization pipeline order (matches log_analyzer.py convention and CLAUDE.md):
  strip ANSI → strip timestamps → strip memory addresses → strip line numbers
  → strip UUIDs → collapse whitespace → SHA-256 hash
"""
from __future__ import annotations

import hashlib
import re

from langchain_core.tools import tool

# ---------------------------------------------------------------------------
# Tool 1: normalize_error_signature
# ---------------------------------------------------------------------------

@tool
def normalize_error_signature(raw_error: str) -> str:
    """Normalize a raw error message or stack trace and return its SHA-256 hex digest.

    Use this tool to produce a stable fingerprint for a test failure so that
    duplicate occurrences of the same underlying error can be detected even when
    volatile tokens (timestamps, memory addresses, UUIDs, line numbers) differ
    between runs.

    Normalization pipeline applied in order:
      1. Strip ANSI escape codes (color/cursor control sequences).
      2. Strip ISO 8601 timestamps (e.g. 2024-01-15T10:30:00.123Z).
      3. Strip bare HH:MM:SS time stamps.
      4. Strip hexadecimal memory addresses (0x followed by 4+ hex digits).
      5. Strip "line N" textual references (case-insensitive).
      6. Strip inline ":N" colon-prefixed line numbers (e.g. :42 or :42:).
      7. Strip UUIDs in the 8-4-4-4-12 lowercase hex format.
      8. Collapse consecutive whitespace to a single space and strip edges.

    The final SHA-256 hex digest is returned as a 64-character lowercase string.
    Two failures that produce the same digest are considered identical errors.

    Args:
        raw_error: Raw error message, stack trace, or concatenation of both.
                   May contain ANSI escape codes and other volatile content.

    Returns:
        64-character lowercase SHA-256 hex digest of the normalized text.
    """
    text = raw_error

    # Step 1: Strip ANSI escape codes (e.g. \x1b[31m, \x1b[0m)
    text = re.sub(r"\x1b\[[0-9;]*[mGKHF]", "", text)

    # Step 2: Strip ISO 8601 timestamps (2024-01-15T10:30:00, .123Z variants)
    text = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[\.\d]*Z?", "", text)

    # Step 3: Strip bare HH:MM:SS time stamps
    text = re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "", text)

    # Step 4: Strip hex memory addresses (0x1a2b3c4d or longer)
    text = re.sub(r"0x[0-9a-fA-F]{4,}", "", text)

    # Step 5: Strip "line N" textual references (e.g. "line 42", "Line 123")
    text = re.sub(r"\bline \d+\b", "", text, flags=re.IGNORECASE)

    # Step 6: Strip inline ":N" or ":N:" colon-prefixed line numbers
    # Matches patterns like :42 or :42: that appear in stack frame paths
    text = re.sub(r":\d+(:\s*)?", ":", text)

    # Step 7: Strip UUIDs (8-4-4-4-12 lowercase hex, e.g. 550e8400-e29b-41d4-...)
    text = re.sub(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        "",
        text,
    )

    # Step 8: Collapse consecutive whitespace and strip leading/trailing space
    text = re.sub(r"\s+", " ", text).strip()

    return hashlib.sha256(text.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tool 2: extract_stack_frames
# ---------------------------------------------------------------------------

@tool
def extract_stack_frames(stack_trace: str) -> list[dict]:
    """Parse a stack trace into a list of structured frame dictionaries.

    Use this tool to convert a raw stack trace string into structured data that
    can be stored, reasoned over, and included in Jira ticket descriptions.
    The tool handles two common stack frame formats:

      Python traceback format:
        File "src/checkout.py", line 42, in process_payment
          result = gateway.charge(amount)

      Compact "at" format (used by some test runners and Java-style traces):
        at src/checkout.py:42

    Each parsed frame is returned as a dict with these keys:
      - "file": str — source file path extracted from the frame line.
      - "line": int | None — line number as an integer, or None if not found.
      - "function": str | None — function/method name, or None if not found.
      - "code": str | None — the source code snippet on the next line (Python
                             tracebacks only), stripped of leading whitespace.

    Frames are returned in the order they appear in the stack trace (outermost
    to innermost for Python, which is oldest-call-first).

    Args:
        stack_trace: Raw stack trace string. May be a Python traceback, a
                     compact "at file:line" listing, or a mix of both.

    Returns:
        List of frame dicts. Returns an empty list if no frames are found.
        Example:
          [
            {
              "file": "src/checkout.py",
              "line": 42,
              "function": "process_payment",
              "code": "result = gateway.charge(amount)",
            }
          ]
    """
    frames: list[dict] = []
    lines = stack_trace.splitlines()

    # Pattern 1: Python traceback — `  File "path", line N, in func_name`
    # The optional leading whitespace allows for indented tracebacks.
    python_frame_re = re.compile(
        r'^\s*File\s+"(?P<file>[^"]+)",\s+line\s+(?P<line>\d+),\s+in\s+(?P<func>\S+)',
        re.IGNORECASE,
    )

    # Pattern 2: compact "at" format — `    at path/to/file.py:42`
    at_frame_re = re.compile(
        r"^\s*at\s+(?P<file>[^\s:]+):(?P<line>\d+)",
        re.IGNORECASE,
    )

    i = 0
    while i < len(lines):
        line = lines[i]

        m = python_frame_re.match(line)
        if m:
            # Peek at the next line for the source code snippet
            code_snippet: str | None = None
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                # Source snippet lines are indented and don't start another frame
                if (
                    next_line
                    and not python_frame_re.match(next_line)
                    and not at_frame_re.match(next_line)
                ):
                    stripped = next_line.strip()
                    # Avoid treating exception lines as code snippets
                    is_exception = stripped.startswith("Traceback") or "Error:" in stripped
                    if stripped and not is_exception:
                        code_snippet = stripped
                        i += 1  # consume the snippet line

            frames.append(
                {
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "function": m.group("func"),
                    "code": code_snippet,
                }
            )
            i += 1
            continue

        m = at_frame_re.match(line)
        if m:
            frames.append(
                {
                    "file": m.group("file"),
                    "line": int(m.group("line")),
                    "function": None,
                    "code": None,
                }
            )
            i += 1
            continue

        i += 1

    return frames


# ---------------------------------------------------------------------------
# Tool 3: classify_error_type
# ---------------------------------------------------------------------------

@tool
def classify_error_type(error_message: str) -> str:
    """Classify an error message into a coarse error type using rule-based heuristics.

    Use this tool as a fast, cheap pre-classifier before invoking Claude for
    full failure classification. It covers the most common error categories
    that appear in CI/CD test output and returns a stable string label that
    can be used to route failures, set initial priorities, or provide context
    to the Claude classifier.

    Classification rules (evaluated in priority order, first match wins):
      1. "AssertionError" in message              → "assertion_failure"
      2. "TimeoutError" or "timed out" in message → "timeout"
      3. "ConnectionError" or "refused" in message → "network_error"
      4. "ImportError" or "ModuleNotFound" in message → "import_error"
      5. None of the above                         → "unknown"

    Matching is case-insensitive so that variations like "assertionerror",
    "TIMED OUT", or "connection refused" are all handled correctly.

    Args:
        error_message: The error message string to classify. Typically the
                       first line of a stack trace or the exception message
                       extracted from test output.

    Returns:
        One of the following exact strings:
          "assertion_failure" | "timeout" | "network_error" | "import_error" | "unknown"
    """
    msg = error_message.lower()

    if "assertionerror" in msg:
        return "assertion_failure"

    if "timeouterror" in msg or "timed out" in msg:
        return "timeout"

    if "connectionerror" in msg or "refused" in msg:
        return "network_error"

    if "importerror" in msg or "modulenotfound" in msg:
        return "import_error"

    return "unknown"


# ---------------------------------------------------------------------------
# Tool 4: extract_test_names_from_log
# ---------------------------------------------------------------------------

@tool
def extract_test_names_from_log(log_text: str) -> list[str]:
    """Extract the names of failing tests from pytest or JUnit-style CI log output.

    Use this tool to pull a structured list of failing test identifiers out of
    raw CI log text. The extracted names can be stored on the triage state,
    included in Jira ticket descriptions, or used to look up historical failure
    data for duplicate detection.

    Supported log formats:

      pytest short-test-summary:
        FAILED tests/test_foo.py::TestClass::test_method - AssertionError: ...
        FAILED tests/test_bar.py::test_standalone

      pytest collected/running lines (also captured for completeness):
        FAILED tests/test_baz.py::test_something

    The function extracts the test node ID (the part before the first " - "
    separator if present) and returns it without surrounding whitespace.
    Duplicate entries are removed while preserving the original order of first
    occurrence.

    Args:
        log_text: Raw CI log text that may contain one or more "FAILED" lines
                  produced by pytest, tox, or a JUnit-compatible test runner.
                  Non-matching lines are silently ignored.

    Returns:
        Deduplicated list of failing test node IDs in order of first appearance.
        Each entry looks like "tests/test_foo.py::TestClass::test_method".
        Returns an empty list if no FAILED lines are found.

    Example:
        Input:
          "FAILED tests/test_checkout.py::test_payment_declined - AssertionError\\n"
          "FAILED tests/test_auth.py::test_login_timeout - TimeoutError\\n"
          "FAILED tests/test_checkout.py::test_payment_declined - AssertionError\\n"

        Output:
          ["tests/test_checkout.py::test_payment_declined",
           "tests/test_auth.py::test_login_timeout"]
    """
    # Match lines that start with "FAILED" (optional leading whitespace)
    # followed by the test node ID, then optionally " - <reason>"
    failed_re = re.compile(
        r"^\s*FAILED\s+(?P<test_id>[^\s].*?)(?:\s+-\s+.+)?$",
        re.MULTILINE,
    )

    seen: set[str] = set()
    results: list[str] = []

    for m in failed_re.finditer(log_text):
        test_id = m.group("test_id").strip()
        if test_id and test_id not in seen:
            seen.add(test_id)
            results.append(test_id)

    return results
