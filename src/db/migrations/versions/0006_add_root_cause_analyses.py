"""add root_cause_analyses table

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-01

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "root_cause_analyses",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "test_failure_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "pipeline_event_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("root_cause_summary", sa.Text(), nullable=False),
        sa.Column("root_cause_category", sa.String(50), nullable=False),
        sa.Column(
            "likely_cause_files",
            postgresql.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column(
            "investigation_steps",
            postgresql.JSON(),
            nullable=False,
            server_default="[]",
        ),
        sa.Column("model_used", sa.String(100), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["pipeline_event_id"],
            ["pipeline_events.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_root_cause_analyses_test_failure_id",
        "root_cause_analyses",
        ["test_failure_id"],
    )
    op.create_index(
        "ix_root_cause_analyses_pipeline_event_id",
        "root_cause_analyses",
        ["pipeline_event_id"],
    )
    op.create_index(
        "ix_root_cause_analyses_root_cause_category",
        "root_cause_analyses",
        ["root_cause_category"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_root_cause_analyses_root_cause_category",
        table_name="root_cause_analyses",
    )
    op.drop_index(
        "ix_root_cause_analyses_pipeline_event_id",
        table_name="root_cause_analyses",
    )
    op.drop_index(
        "ix_root_cause_analyses_test_failure_id",
        table_name="root_cause_analyses",
    )
    op.drop_table("root_cause_analyses")
