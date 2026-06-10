"""One-time data fix: remove duplicate ``test_failures`` rows created by repeated
``/api/v1/failures/{id}/retriage`` calls during E2E testing, and correct the
status of the original rows.

Background
-----------
During E2E testing, ``POST /api/v1/failures/{id}/retriage`` was called twice
against ``pipeline_event_id = e158d74c-ccdd-4267-bdb2-a4d38f71ba12``. Each call
re-fetched/re-parsed CI logs and INSERTED NEW ``test_failures`` rows instead of
re-triaging the existing rows in place, leaving 4 duplicate/orphaned rows.

The bug that caused ``test_failures.status`` to remain stuck at "triaging"
instead of advancing to "triaged" once ``run_triage()`` finishes was fixed in
commit ``049b479``, but that fix does not retroactively apply to this
already-completed pipeline_event's 2 original rows.

This script:

1. Deletes the 4 duplicate ``test_failures`` rows (FK children — agent_runs,
   failure_classifications, heal_suggestions, notifications,
   root_cause_analyses — all use ``ondelete="CASCADE"`` and are removed
   automatically by Postgres).
2. Sets ``status = 'triaged'`` on the 2 original rows.
3. Verifies the final state.

Usage:
    uv run python scripts/cleanup_duplicate_failures.py

This is a one-time data fix — do NOT wire this into migrations or startup logic.
"""
from __future__ import annotations

import asyncio
import uuid

import structlog
from sqlalchemy import select

from src.db.session import get_session_factory, reset_engine
from src.models.test_failure import TestFailure

logger = structlog.get_logger(__name__)

PIPELINE_EVENT_ID = uuid.UUID("e158d74c-ccdd-4267-bdb2-a4d38f71ba12")

DUPLICATE_IDS = [
    uuid.UUID("142e39e1-4ea4-4789-bcfb-3dca5c4258ce"),
    uuid.UUID("dd7ef628-38af-48ed-9579-7d9ec69cb5cc"),
    uuid.UUID("abec3d7a-0e86-456c-b57e-96b264e3d472"),
    uuid.UUID("d4ada63f-09e5-480b-a175-3486de60a0e0"),
]

ORIGINAL_IDS = [
    uuid.UUID("1849846c-4bd0-4261-acdf-0339fe913e06"),
    uuid.UUID("62199d33-fd36-4b46-97dc-62da9fc69e32"),
]


async def main() -> None:
    await reset_engine()
    session_factory = get_session_factory()

    async with session_factory() as session, session.begin():
        # --- Delete the 4 duplicate rows. FK children (agent_runs,
        # failure_classifications, heal_suggestions, notifications,
        # root_cause_analyses) cascade-delete via ondelete="CASCADE".
        duplicates = (
            (
                await session.execute(
                    select(TestFailure).where(TestFailure.id.in_(DUPLICATE_IDS))
                )
            )
            .scalars()
            .all()
        )
        logger.info(
            "deleting_duplicate_test_failures",
            found=len(duplicates),
            ids=[str(d.id) for d in duplicates],
        )
        for row in duplicates:
            await session.delete(row)

        # --- Correct the status of the 2 original rows.
        originals = (
            (
                await session.execute(
                    select(TestFailure).where(TestFailure.id.in_(ORIGINAL_IDS))
                )
            )
            .scalars()
            .all()
        )
        logger.info(
            "updating_original_test_failures_status",
            found=len(originals),
            ids=[str(o.id) for o in originals],
        )
        for row in originals:
            row.status = "triaged"

    # session.begin() block above commits on success / rolls back on error.

    # --- Verification (separate session, after commit) ---
    async with session_factory() as session:
        remaining_duplicates = (
            (
                await session.execute(
                    select(TestFailure.id).where(TestFailure.id.in_(DUPLICATE_IDS))
                )
            )
            .scalars()
            .all()
        )
        updated_originals = (
            (
                await session.execute(
                    select(TestFailure.id, TestFailure.status).where(
                        TestFailure.id.in_(ORIGINAL_IDS)
                    )
                )
            )
            .all()
        )
        pipeline_failures = (
            (
                await session.execute(
                    select(TestFailure.id).where(
                        TestFailure.pipeline_event_id == PIPELINE_EVENT_ID
                    )
                )
            )
            .scalars()
            .all()
        )

        logger.info(
            "verification",
            remaining_duplicates=[str(i) for i in remaining_duplicates],
            updated_originals=[(str(i), s) for i, s in updated_originals],
            pipeline_event_failure_count=len(pipeline_failures),
            pipeline_event_failure_ids=[str(i) for i in pipeline_failures],
        )

        assert not remaining_duplicates, "duplicate rows still present!"
        assert all(s == "triaged" for _, s in updated_originals), (
            "original rows not all marked triaged!"
        )
        assert len(pipeline_failures) == 2, (
            f"expected 2 test_failures for pipeline_event, found {len(pipeline_failures)}"
        )

    await reset_engine()
    logger.info("cleanup_complete")


if __name__ == "__main__":
    asyncio.run(main())
