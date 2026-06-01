"""add test_screenshots table

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-01

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "test_screenshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("test_failure_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(500), nullable=False),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(1000), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("baseline_screenshot_id", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["baseline_screenshot_id"],
            ["test_screenshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_test_screenshots_test_failure_id",
        "test_screenshots",
        ["test_failure_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_test_screenshots_test_failure_id", table_name="test_screenshots")
    op.drop_table("test_screenshots")
