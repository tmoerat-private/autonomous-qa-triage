from __future__ import annotations

import re

import structlog

from src.schemas.webhook_payloads import GitHubActionsWebhookPayload, ParsedTestFailure

logger = structlog.get_logger(__name__)

# Matches the ISO-8601 timestamp prefix that GitHub Actions prepends to every
# log line, e.g. "2024-01-15T10:23:45.1234567Z ".
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z\s?")

# Strips ANSI color/cursor escape codes (e.g. "\x1b[2m", "\x1b[31m"), which
# Playwright's list reporter embeds heavily in its failure output.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")

# Matches pytest FAILED/ERROR output lines:
#   FAILED tests/unit/test_foo.py::TestBar::test_baz - AssertionError: ...
#   ERROR  tests/unit/test_foo.py::TestBar::test_baz
_FAILURE_RE = re.compile(
    r"^(FAILED|ERROR)\s+([\w/\.]+::[\w:]+)(?:\s+-\s+(.+))?$"
)

# Lines that indicate the start of a traceback block
_TRACEBACK_MARKERS = frozenset(["FAILURES", "ERRORS", "short test summary info"])

# Matches Playwright's list-reporter failure entries:
#   1) [chromium] > tests/navigation/navigation.spec.ts:39:7 > Sidebar navigation > dark mode toggle
_PLAYWRIGHT_FAILURE_RE = re.compile(
    r"^\s*\d+\)\s+\[(?P<project>[^\]]+)\]\s+›\s+(?P<rest>.+?)\s*$"  # noqa: RUF001
)

# Matches the trailing "<file>:<line>:<col>" suffix on the first segment of a
# Playwright failure entry, e.g. "tests/navigation/navigation.spec.ts:39:7".
_PLAYWRIGHT_FILE_RE = re.compile(r"^(?P<file>.+\.(?:spec|test)\.[jt]sx?):\d+:\d+$")

# Matches Playwright's run summary lines, e.g. "1 failed" / "70 passed (3.3m)".
_PLAYWRIGHT_SUMMARY_RE = re.compile(r"^\s*\d+\s+(failed|passed|skipped|flaky)\b")


class GitHubActionsParser:
    """Parse GitHub Actions log text into structured test-failure records.

    GitHub Actions prepends an ISO-8601 timestamp to every log line.  This
    parser strips those prefixes before applying log parsing.

    Two log formats are supported:

    1. **pytest** — ``FAILED`` / ``ERROR`` lines in the standard pytest
       short-summary format (``path::class::method - message``), as produced
       by Python test suites.
    2. **Playwright** — numbered failure entries from Playwright's list
       reporter (``N) [project] > path.spec.ts:line:col > ... > test name``),
       as produced by JS/TS end-to-end test suites.

    ``parse_failures`` tries the pytest format first; if it finds nothing, it
    falls back to the Playwright format. Detection is automatic — callers do
    not need to know which format a given log uses.
    """

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def parse_failures(self, log_text: str) -> list[ParsedTestFailure]:
        """Extract test failures from a GitHub Actions log blob.

        The method:
        1. Strips the timestamp prefix from every line.
        2. Tries the pytest ``FAILED``/``ERROR`` format first.
        3. If no failures were found, falls back to Playwright's
           list-reporter format.

        Args:
            log_text: Raw combined log text from a workflow run (may contain
                logs from multiple jobs joined with ``"\\n---\\n"``).

        Returns:
            Deduplicated list of ``ParsedTestFailure`` objects.  Returns
            ``[]`` when no failures are found in either format.
        """
        if not log_text:
            return []

        clean_lines = [_TIMESTAMP_RE.sub("", line) for line in log_text.splitlines()]

        failures = self._parse_pytest_failures(clean_lines)
        if not failures:
            failures = _parse_playwright_failures(clean_lines)

        logger.debug(
            "github_actions.parser.parse_failures.done",
            failures_found=len(failures),
        )
        return failures

    def extract_run_info(
        self, payload: GitHubActionsWebhookPayload
    ) -> tuple[str, int]:
        """Return the ``(repo_full_name, run_id)`` pair from a webhook payload.

        Args:
            payload: A validated ``GitHubActionsWebhookPayload`` instance.

        Returns:
            A tuple of ``(repo_full_name, run_id)``.
        """
        repo_full_name = payload.workflow_run.repository.full_name
        run_id = payload.workflow_run.id
        return repo_full_name, run_id

    # ------------------------------------------------------------------
    # pytest format
    # ------------------------------------------------------------------

    def _parse_pytest_failures(self, clean_lines: list[str]) -> list[ParsedTestFailure]:
        """Scan ``clean_lines`` for pytest ``FAILED``/``ERROR`` markers.

        Collects any indented stack-trace lines that follow each marker and
        deduplicates by ``test_name`` (keeps the first occurrence).
        """
        failures: list[ParsedTestFailure] = []
        seen_names: set[str] = set()

        i = 0
        while i < len(clean_lines):
            line = clean_lines[i].rstrip()
            match = _FAILURE_RE.match(line)
            if match:
                _marker, test_path, error_message = match.groups()

                # Parse the path::class::method structure
                test_file, test_suite, test_name = _split_test_path(test_path)

                # Collect the stack trace — indented lines that follow
                trace_lines: list[str] = []
                j = i + 1
                while j < len(clean_lines):
                    next_line = clean_lines[j]
                    # Stop at the next FAILED/ERROR line or section separator
                    if _FAILURE_RE.match(next_line.rstrip()):
                        break
                    if next_line.strip() in _TRACEBACK_MARKERS:
                        break
                    # Only accumulate non-empty lines that look like a trace
                    if next_line.startswith((" ", "\t")) or next_line.startswith("E "):
                        trace_lines.append(next_line)
                    elif next_line.strip() == "":
                        # Blank line — include it but stop if followed by a
                        # non-indented, non-blank line (end of trace block).
                        if j + 1 < len(clean_lines):
                            peek = clean_lines[j + 1]
                            if peek and not peek.startswith((" ", "\t", "E ")):
                                break
                        trace_lines.append(next_line)
                    else:
                        break
                    j += 1

                stack_trace = "\n".join(trace_lines).strip() or None

                if test_name not in seen_names:
                    seen_names.add(test_name)
                    failures.append(
                        ParsedTestFailure(
                            test_name=test_name,
                            test_suite=test_suite,
                            test_file=test_file,
                            error_message=error_message,
                            stack_trace=stack_trace,
                        )
                    )

            i += 1

        return failures


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _split_test_path(test_path: str) -> tuple[str | None, str | None, str]:
    """Split a pytest node ID into ``(test_file, test_suite, test_name)``.

    Handles the following node-ID formats:
    - ``tests/unit/test_foo.py::test_function``          → file, None, function
    - ``tests/unit/test_foo.py::TestClass::test_method`` → file, class, method
    - ``TestClass::test_method`` (no file component)     → None, class, method
    - ``test_function`` (bare name)                      → None, None, name

    The ``test_name`` portion is always the last ``::``-separated segment.
    """
    parts = test_path.split("::")

    if len(parts) == 1:
        # Bare test name with no path or class
        return None, None, parts[0]

    # The first segment is the test file only if it ends with ".py"
    if parts[0].endswith(".py"):
        test_file: str | None = parts[0]
        remaining = parts[1:]
    else:
        test_file = None
        remaining = parts

    if len(remaining) == 1:
        return test_file, None, remaining[0]

    # remaining[0] is the class name, remaining[-1] is the method
    test_suite: str | None = remaining[0]
    test_name = "::".join(remaining)  # preserve full qualified name
    return test_file, test_suite, test_name


def _parse_playwright_failures(clean_lines: list[str]) -> list[ParsedTestFailure]:
    """Scan ``clean_lines`` for Playwright list-reporter failure entries.

    Each failure entry looks like::

        1) [chromium] > tests/navigation/navigation.spec.ts:39:7 > Sidebar nav > dark mode toggle

          Error: expect(locator).toBeVisible() failed
          ...

    The numbered entry is split on the ``>`` separator: the first segment is
    the ``<file>:<line>:<col>`` location, any middle segments are describe-block
    names, and the last segment is the test name. ``test_suite`` combines the
    browser project name with any describe-block names. The indented detail
    block that follows (error message, call log, code frame, retries) is
    captured as ``stack_trace``, with the first ``Error: ...`` line lifted out
    as ``error_message``. ANSI escape codes are stripped before matching.
    Deduplicates by ``test_name`` (keeps the first occurrence).
    """
    failures: list[ParsedTestFailure] = []
    seen_names: set[str] = set()

    i = 0
    while i < len(clean_lines):
        line = _ANSI_RE.sub("", clean_lines[i]).rstrip()
        match = _PLAYWRIGHT_FAILURE_RE.match(line)
        if match:
            project = match.group("project").strip()
            rest = match.group("rest").strip()
            segments = [s.strip() for s in rest.split("›")]  # noqa: RUF001

            file_part = segments[0]
            file_match = _PLAYWRIGHT_FILE_RE.match(file_part)
            test_file = file_match.group("file") if file_match else file_part

            remaining = segments[1:]
            if remaining:
                test_name = remaining[-1]
                describe_blocks = remaining[:-1]
            else:
                test_name = file_part
                describe_blocks = []

            test_suite = " › ".join([project, *describe_blocks])  # noqa: RUF001

            # Collect the detail block — everything up to the next numbered
            # failure entry or the run summary ("N failed" / "N passed").
            detail_lines: list[str] = []
            j = i + 1
            while j < len(clean_lines):
                next_clean = _ANSI_RE.sub("", clean_lines[j])
                next_stripped = next_clean.rstrip()
                if _PLAYWRIGHT_FAILURE_RE.match(next_stripped):
                    break
                if _PLAYWRIGHT_SUMMARY_RE.match(next_stripped):
                    break
                detail_lines.append(next_clean)
                j += 1

            stack_trace = "\n".join(detail_lines).strip() or None

            error_message: str | None = None
            for detail_line in detail_lines:
                stripped = detail_line.strip()
                if stripped.startswith("Error:"):
                    error_message = stripped[len("Error:") :].strip()
                    break

            if test_name not in seen_names:
                seen_names.add(test_name)
                failures.append(
                    ParsedTestFailure(
                        test_name=test_name,
                        test_suite=test_suite,
                        test_file=test_file,
                        error_message=error_message,
                        stack_trace=stack_trace,
                    )
                )

            i = j
            continue

        i += 1

    return failures
