"""Create ``national_scenarios`` and ``scenario_share_links``.

Revision ID: 0012_national_scenarios
Revises: 0011_audit_actor
Create Date: 2026-11-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_national_scenarios"
down_revision: str | None = "0011_audit_actor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "national_scenarios",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("parameters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.Text(), nullable=False),
        sa.Column("net_impact_central", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_national_scenarios_user_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["model_version_id"],
            ["model_versions.id"],
            name="fk_national_scenarios_model_version_id_model_versions",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="ck_national_scenarios_name"),
    )
    op.create_index("ix_national_scenarios_user_id", "national_scenarios", ["user_id"])

    op.create_table(
        "scenario_share_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("scenario_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["scenario_id"],
            ["national_scenarios.id"],
            name="fk_scenario_share_links_scenario_id_national_scenarios",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("scenario_id", name="uq_scenario_share_links_scenario_id"),
    )
    # ``unique=True, index=True`` côté modèle => index unique.
    op.create_index(
        "ix_scenario_share_links_token_hash",
        "scenario_share_links",
        ["token_hash"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_scenario_share_links_token_hash", table_name="scenario_share_links")
    op.drop_table("scenario_share_links")
    op.drop_index("ix_national_scenarios_user_id", table_name="national_scenarios")
    op.drop_table("national_scenarios")
