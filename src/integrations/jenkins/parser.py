from __future__ import annotations

import re

import structlog

from src.schemas.webhook_payloads import JenkinsWebhookPayload, ParsedTestFailure

logger = structlog.get_logger(__name__)

# Matches lines like:
#   FAILED tests/path/test_file.py::TestClass::test_method - ErrorType: message
#   FAILED tests/path/test_file.py::test_function - ErrorType: message
#   ERROR  tests/path/test_file.py::test_function
_FAILURE_RE = re.compile(
    r"^(FAILED|ERROR)\s+([\w/\.]+::[\w:]+)(?:\s+-\s+(.+))?$"
)

# Lines that mark the end of a stack-trace block
_SECTION_SEP_RE = re.compile(r"^={5,}")
_NEXT_RESULT_RE = re.compile(r"^(FAILED|ERROR|PASSED)\s+")


def _parse_test_id(test_id: str) -> tuple[str, str | None, str]:
    """Split a pytest test ID into (test_file, test_suite, test_name).

    Examples::

        "tests/foo/test_bar.py::TestBar::test_something"
        -> ("tests/foo/test_bar.py", "TestBar", "test_something")

        "tests/foo/test_bar.py::test_something"
        -> ("tests/foo/test_bar.py", None, "test_something")
    """
    parts = test_id.split("::")
    test_file = parts[0]

    if len(parts) >= 3:
        # Has a class component: file::Class::method
        test_suite: str | None = parts[1]
        test_name = parts[-1]
    elif len(parts) == 2:
        # Plain function: file::function
        test_suite = None
        test_name = parts[1]
    else:
        # Degenerate — no "::" at all; use filename as name
        test_suite = None
        test_name = test_file

    return test_file, test_suite, test_name


class JenkinsParser:
    """Parse pytest/unittest output from a Jenkins console log."""

    def parse_failures(self, console_log: str) -> list[ParsedTestFailure]:
        """Extract individual test failures from a Jenkins console log.

        Scans the log line by line for pytest ``FAILED``/``ERROR`` markers,
        collects the trailing stack-trace lines, and returns a deduplicated
        list of ``ParsedTestFailure`` objects.

        Args:
            console_log: Raw plain-text console output from Jenkins.

        Returns:
            List of parsed failures; empty list if none are found or the log
            is empty.  Never raises.
        """
        if not console_log:
            return []

        try:
            return self._parse(console_log)
        except Exception:
            logger.warning(
                "jenkins.parser.parse_failures.unexpected_error",
                exc_info=True,
            )
            return []

    def _parse(self, console_log: str) -> list[ParsedTestFailure]:
        lines = console_log.splitlines()
        failures: list[ParsedTestFailure] = []
        seen_test_names: set[str] = set()

        i = 0
        while i < len(lines):
            line = lines[i].rstrip()
            match = _FAILURE_RE.match(line)

            if match:
                _marker, test_id, error_msg = match.groups()
                test_file, test_suite, test_name = _parse_test_id(test_id)

                # Collect subsequent stack-trace lines
                trace_lines: list[str] = []
                i += 1
                while i < len(lines):
                    next_line = lines[i].rstrip()
                    if _SECTION_SEP_RE.match(next_line) or _NEXT_RESULT_RE.match(next_line):
                        break
                    trace_lines.append(next_line)
                    i += 1

                stack_trace = "\n".join(trace_lines).strip() or None

                # Deduplicate by test_name — keep first occurrence
                if test_name not in seen_test_names:
                    seen_test_names.add(test_name)
                    failures.append(
                        ParsedTestFailure(
                            test_name=test_name,
                            test_suite=test_suite,
                            test_file=test_file,
                            error_message=error_msg or None,
                            stack_trace=stack_trace,
                        )
                    )
            else:
                i += 1

        logger.debug(
            "jenkins.parser.parse_failures.complete",
            failures_found=len(failures),
        )
        return failures

    def extract_job_info(self, payload: JenkinsWebhookPayload) -> tuple[str, int]:
        """Return ``(job_name, build_number)`` from a Jenkins webhook payload.

        Args:
            payload: Validated ``JenkinsWebhookPayload`` instance.

        Returns:
            A two-tuple of ``(job_name, build_number)``.
        """
        return payload.name, payload.build.number
