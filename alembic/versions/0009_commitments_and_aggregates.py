"""Citizen commitments, aggregated impacts and minimal profile fields.

Revision ID: 0009_commitments_and_aggregates
Revises: 0008_economic_data
Create Date: 2026-11-07 00:30:00.000000

Strictly additive. Adds ``users.country`` / ``region`` / ``household_adults``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_commitments_and_aggregates"
down_revision: str | None = "0008_economic_data"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("country", sa.String(length=2), server_default="FR", nullable=False)
    )
    op.add_column("users", sa.Column("region", sa.String(length=8), nullable=True))
    op.add_column(
        "users", sa.Column("household_adults", sa.Integer(), server_default="1", nullable=False)
    )
    op.create_check_constraint(
        "ck_users_household_adults", "users", "household_adults BETWEEN 1 AND 10"
    )

    op.create_table(
        "citizen_commitments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("reduction_percentage", sa.Numeric(7, 4), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["consumption_categories.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category_id", name="uq_citizen_commitments_user_category"),
        sa.CheckConstraint(
            "reduction_percentage >= 0 AND reduction_percentage <= 100",
            name="ck_citizen_commitments_percentage",
        ),
        sa.CheckConstraint("end_date > start_date", name="ck_citizen_commitments_dates"),
        sa.CheckConstraint(
            "status IN ('active','suspended')", name="ck_citizen_commitments_status"
        ),
    )
    op.create_index("ix_citizen_commitments_user_id", "citizen_commitments", ["user_id"])
    op.create_index("ix_citizen_commitments_category_id", "citizen_commitments", ["category_id"])

    op.create_table(
        "aggregated_impacts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("aggregation_period", sa.String(length=16), nullable=False),
        sa.Column("geographical_level", sa.String(length=16), nullable=False),
        sa.Column("geographical_code", sa.String(length=8), nullable=False),
        sa.Column("participant_count", sa.Integer(), nullable=False),
        sa.Column("active_commitments", sa.Integer(), nullable=False),
        sa.Column("consumption_reduction", sa.Numeric(18, 2), nullable=False),
        sa.Column("personal_savings", sa.Numeric(18, 2), nullable=False),
        sa.Column("direct_fiscal_impact", sa.Numeric(18, 2), nullable=False),
        sa.Column("indirect_fiscal_impact", sa.Numeric(18, 2), nullable=False),
        sa.Column("induced_fiscal_impact", sa.Numeric(18, 2), nullable=False),
        sa.Column("net_fiscal_impact", sa.Numeric(18, 2), nullable=False),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "calculated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_aggregated_impacts_aggregation_period", "aggregated_impacts", ["aggregation_period"]
    )
    op.create_index("ix_aggregated_impacts_calculated_at", "aggregated_impacts", ["calculated_at"])


def downgrade() -> None:
    op.drop_index("ix_aggregated_impacts_calculated_at", table_name="aggregated_impacts")
    op.drop_index("ix_aggregated_impacts_aggregation_period", table_name="aggregated_impacts")
    op.drop_table("aggregated_impacts")
    op.drop_index("ix_citizen_commitments_category_id", table_name="citizen_commitments")
    op.drop_index("ix_citizen_commitments_user_id", table_name="citizen_commitments")
    op.drop_table("citizen_commitments")
    op.drop_constraint("ck_users_household_adults", "users", type_="check")
    op.drop_column("users", "household_adults")
    op.drop_column("users", "region")
    op.drop_column("users", "country")
