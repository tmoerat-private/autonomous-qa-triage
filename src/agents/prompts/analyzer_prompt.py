"""Error normalization pipeline for generating stable error signatures.

Raw error messages and stack traces contain volatile tokens that change between
runs even when the underlying failure is identical: timestamps, memory addresses,
UUIDs, line numbers assigned by a garbage collector, and ANSI colour codes added
by test runners.  Storing or comparing raw text would produce a new "unique" error
for every run of the same bug, defeating duplicate detection.

This module documents the ordered normalization steps applied by log_analyzer to
produce a deterministic, human-readable representation of an error.  The final
normalized string is then SHA-256 hashed to create the ``error_signature`` field
in TriageState, which the duplicate_detector node uses for exact-match dedup.

Steps are applied in the order listed in NORMALIZATION_STEPS.
"""

from __future__ import annotations

NORMALIZATION_STEPS: list[str] = [
    "strip_ansi",
    "strip_iso_timestamps",
    "strip_time_timestamps",
    "strip_memory_addresses",
    "strip_line_numbers",
    "strip_uuids",
    "collapse_whitespace",
]
