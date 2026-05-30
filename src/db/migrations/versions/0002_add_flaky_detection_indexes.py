"""add flaky detection indexes

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-30

"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Index on test_failures(test_name) — supports per-test flakiness queries
    op.create_index(
        "ix_test_failures_test_name",
        "test_failures",
        ["test_name"],
    )

    # Index on test_failures(created_at) — supports lookback-window date filtering
    op.create_index(
        "ix_test_failures_created_at",
        "test_failures",
        ["created_at"],
    )

    # Index on pipeline_events(repository) — supports repository-scoped run counts
    op.create_index(
        "ix_pipeline_events_repository",
        "pipeline_events",
        ["repository"],
    )

    # Index on pipeline_events(created_at) — supports lookback-window date filtering
    op.create_index(
        "ix_pipeline_events_created_at",
        "pipeline_events",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_pipeline_events_created_at", table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_repository", table_name="pipeline_events")
    op.drop_index("ix_test_failures_created_at", table_name="test_failures")
    op.drop_index("ix_test_failures_test_name", table_name="test_failures")
