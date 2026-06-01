"""add release_scores table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-01

"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "release_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("commit_sha", sa.String(40), nullable=False),
        sa.Column("repository", sa.String(500), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("risk_level", sa.String(20), nullable=False),
        sa.Column("risk_summary", sa.Text(), nullable=True),
        sa.Column("total_failures", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("product_bug_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("flaky_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("env_issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("infra_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duplicate_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("avg_confidence", sa.Float(), nullable=True),
        sa.Column(
            "scored_at",
            sa.TIMESTAMP(timezone=True),
            server_default=sa.func.now(),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_sha", "repository", name="uq_release_scores_commit_repo"),
    )
    op.create_index(
        "ix_release_scores_commit_sha",
        "release_scores",
        ["commit_sha"],
    )
    op.create_index(
        "ix_release_scores_repository",
        "release_scores",
        ["repository"],
    )
    op.create_index(
        "ix_release_scores_scored_at",
        "release_scores",
        ["scored_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_release_scores_scored_at", table_name="release_scores")
    op.drop_index("ix_release_scores_repository", table_name="release_scores")
    op.drop_index("ix_release_scores_commit_sha", table_name="release_scores")
    op.drop_table("release_scores")
