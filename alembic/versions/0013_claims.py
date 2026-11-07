"""Create ``claims``, ``claim_supports``, ``claim_comments`` and ``content_reports``.

Revision ID: 0013_claims
Revises: 0012_national_scenarios
Create Date: 2026-11-11 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_claims"
down_revision: str | None = "0012_national_scenarios"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )


def upgrade() -> None:
    op.create_table(
        "claims",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("support_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("report_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("moderation_reason", sa.String(length=500), nullable=True),
        sa.Column("hidden_by", sa.String(length=8), nullable=True),
        sa.Column("moderation_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_claims_author_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["consumption_categories.id"],
            name="fk_claims_category_id_consumption_categories",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint("status IN ('draft','published','hidden','closed')", name="ck_claims_status"),
        sa.CheckConstraint("support_count >= 0", name="ck_claims_support_count"),
        sa.CheckConstraint("report_count >= 0", name="ck_claims_report_count"),
        sa.CheckConstraint("char_length(title) BETWEEN 5 AND 120", name="ck_claims_title_len"),
        sa.CheckConstraint("char_length(body) BETWEEN 20 AND 2000", name="ck_claims_body_len"),
        sa.UniqueConstraint("author_id", "fingerprint", name="uq_claims_author_fingerprint"),
    )
    op.create_index("ix_claims_author_id", "claims", ["author_id"])
    op.create_index(
        "ix_claims_published_date",
        "claims",
        ["published_at", "id"],
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "ix_claims_published_support",
        "claims",
        ["support_count", "id"],
        postgresql_where=sa.text("status = 'published'"),
    )
    op.create_index(
        "ix_claims_published_category",
        "claims",
        ["category_id", "id"],
        postgresql_where=sa.text("status = 'published'"),
    )

    op.create_table(
        "claim_supports",
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        _created_at(),
        sa.PrimaryKeyConstraint("claim_id", "user_id", name="pk_claim_supports"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_claim_supports_claim_id_claims",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_claim_supports_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index("ix_claim_supports_user", "claim_supports", ["user_id", "claim_id"])

    op.create_table(
        "claim_comments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("claim_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="visible", nullable=False),
        sa.Column("report_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("moderation_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_claim_comments_claim_id_claims",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["users.id"],
            name="fk_claim_comments_author_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("char_length(body) BETWEEN 1 AND 1000", name="ck_claim_comments_body_len"),
        sa.CheckConstraint("status IN ('visible','hidden')", name="ck_claim_comments_status"),
        sa.CheckConstraint("report_count >= 0", name="ck_claim_comments_report_count"),
        sa.UniqueConstraint("author_id", "fingerprint", name="uq_claim_comments_author_fingerprint"),
    )
    op.create_index("ix_claim_comments_claim_id", "claim_comments", ["claim_id"])

    op.create_table(
        "content_reports",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("reporter_id", sa.Integer(), nullable=False),
        sa.Column("claim_id", sa.Integer(), nullable=True),
        sa.Column("comment_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=24), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _created_at(),
        sa.ForeignKeyConstraint(
            ["reporter_id"],
            ["users.id"],
            name="fk_content_reports_reporter_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["claims.id"],
            name="fk_content_reports_claim_id_claims",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["comment_id"],
            ["claim_comments.id"],
            name="fk_content_reports_comment_id_claim_comments",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "(claim_id IS NULL) <> (comment_id IS NULL)", name="ck_content_reports_one_target"
        ),
        sa.CheckConstraint(
            "reason IN ('illegal','hate_harassment','off_topic','duplicate','other')",
            name="ck_content_reports_reason",
        ),
    )
    op.create_index(
        "uq_content_reports_claim",
        "content_reports",
        ["reporter_id", "claim_id"],
        unique=True,
        postgresql_where=sa.text("claim_id IS NOT NULL"),
    )
    op.create_index(
        "uq_content_reports_comment",
        "content_reports",
        ["reporter_id", "comment_id"],
        unique=True,
        postgresql_where=sa.text("comment_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_content_reports_comment", table_name="content_reports")
    op.drop_index("uq_content_reports_claim", table_name="content_reports")
    op.drop_table("content_reports")
    op.drop_index("ix_claim_comments_claim_id", table_name="claim_comments")
    op.drop_table("claim_comments")
    op.drop_index("ix_claim_supports_user", table_name="claim_supports")
    op.drop_table("claim_supports")
    op.drop_index("ix_claims_published_category", table_name="claims")
    op.drop_index("ix_claims_published_support", table_name="claims")
    op.drop_index("ix_claims_published_date", table_name="claims")
    op.drop_index("ix_claims_author_id", table_name="claims")
    op.drop_table("claims")
