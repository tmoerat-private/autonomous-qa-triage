"""add heal_suggestions and rerun_requests tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. heal_suggestions  (FK → test_failures)
    # ------------------------------------------------------------------
    op.create_table(
        "heal_suggestions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_failure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("suggestion", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("affected_file", sa.String(500), nullable=True),
        sa.Column("fix_snippet", sa.Text(), nullable=True),
        sa.Column("accepted", sa.Boolean(), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["test_failure_id"],
            ["test_failures.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_heal_suggestions_test_failure_id",
        "heal_suggestions",
        ["test_failure_id"],
    )

    # ------------------------------------------------------------------
    # 2. rerun_requests  (FK → test_failures)
    # ------------------------------------------------------------------
    op.create_table(
        "rerun_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_failure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False),
        sa.Column("triggered_job_id", sa.String(255), nullable=True),
        sa.Column(
            "trigger_reason",
            sa.String(50),
            nullable=False,
            server_default="flaky_detected",
        ),
        sa.Column(
            "status",
            sa.String(50),
            nullable=False,
            server_default="triggered",
        ),
        sa.Column(
            "triggered_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["test_failure_id"],
            ["test_failures.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_rerun_requests_test_failure_id",
        "rerun_requests",
        ["test_failure_id"],
    )


def downgrade() -> None:
    # Drop in reverse creation order (children before parents if applicable)
    op.drop_index("ix_rerun_requests_test_failure_id", table_name="rerun_requests")
    op.drop_table("rerun_requests")
    op.drop_index("ix_heal_suggestions_test_failure_id", table_name="heal_suggestions")
    op.drop_table("heal_suggestions")
