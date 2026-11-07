"""Economic datasets, fiscal parameters, model versions and simulations.

Revision ID: 0008_economic_data
Revises: 0007_user_roles
Create Date: 2026-11-07 00:20:00.000000

Strictly additive.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_economic_data"
down_revision: str | None = "0007_user_roles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "economic_datasets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("source", sa.String(length=255), nullable=False),
        sa.Column("source_url", sa.String(length=1024), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("reference_year", sa.Integer(), nullable=False),
        sa.Column("methodology", sa.Text(), nullable=False),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column(
            "imported_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("imported_by_id", sa.Integer(), nullable=True),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validation_errors", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["imported_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", "version", name="uq_economic_datasets_name_version"),
        sa.CheckConstraint(
            "validation_status IN ('draft','validated','rejected')",
            name="ck_economic_datasets_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_economic_datasets_version_positive"),
    )
    op.create_index("ix_economic_datasets_name", "economic_datasets", ["name"])

    op.create_table(
        "fiscal_parameters",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=True),
        sa.Column("parameter_type", sa.String(length=64), nullable=False),
        sa.Column("parameter_key", sa.String(length=128), nullable=False),
        sa.Column("value_low", sa.Numeric(24, 10), nullable=False),
        sa.Column("parameter_value", sa.Numeric(24, 10), nullable=False),
        sa.Column("value_high", sa.Numeric(24, 10), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("confidence_level", sa.String(length=16), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["dataset_id"], ["economic_datasets.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["consumption_categories.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id",
            "category_id",
            "parameter_type",
            "parameter_key",
            name="uq_fiscal_parameters_identity",
        ),
        sa.CheckConstraint(
            "confidence_level IN ('high','medium','low','provisional')",
            name="ck_fiscal_parameters_confidence",
        ),
    )
    op.create_index("ix_fiscal_parameters_dataset_id", "fiscal_parameters", ["dataset_id"])
    op.create_index("ix_fiscal_parameters_parameter_type", "fiscal_parameters", ["parameter_type"])

    op.create_table(
        "model_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["published_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("label"),
        sa.CheckConstraint("status IN ('draft','published')", name="ck_model_versions_status"),
        sa.CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)",
            name="ck_model_versions_published_at",
        ),
    )

    op.create_table(
        "model_version_datasets",
        sa.Column("model_version_id", sa.Integer(), nullable=False),
        sa.Column("dataset_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dataset_id"], ["economic_datasets.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("model_version_id", "dataset_id"),
    )

    op.create_table(
        "economic_simulations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("model_version_id", sa.Integer(), nullable=True),
        sa.Column("model_version", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.String(length=255), nullable=False),
        sa.Column("scenario_type", sa.String(length=16), nullable=False),
        sa.Column("input_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_version_id"], ["model_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "scenario_type IN ('personal','conservative','central','high_impact')",
            name="ck_economic_simulations_scenario",
        ),
    )
    op.create_index("ix_economic_simulations_user_id", "economic_simulations", ["user_id"])
    op.create_index(
        "ix_economic_simulations_model_version_id", "economic_simulations", ["model_version_id"]
    )

    op.create_table(
        "simulation_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("simulation_id", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.Integer(), nullable=False),
        sa.Column("personal_savings", sa.Numeric(14, 2), nullable=False),
        sa.Column("direct_fiscal_impact", sa.Numeric(14, 2), nullable=False),
        sa.Column("indirect_fiscal_impact", sa.Numeric(14, 2), nullable=False),
        sa.Column("induced_fiscal_impact", sa.Numeric(14, 2), nullable=False),
        sa.Column("additional_public_expenditure", sa.Numeric(14, 2), nullable=False),
        sa.Column("net_fiscal_impact", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence_low", sa.Numeric(14, 2), nullable=False),
        sa.Column("confidence_high", sa.Numeric(14, 2), nullable=False),
        sa.ForeignKeyConstraint(["simulation_id"], ["economic_simulations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["category_id"], ["consumption_categories.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_simulation_results_simulation_id", "simulation_results", ["simulation_id"])


def downgrade() -> None:
    op.drop_index("ix_simulation_results_simulation_id", table_name="simulation_results")
    op.drop_table("simulation_results")
    op.drop_index("ix_economic_simulations_model_version_id", table_name="economic_simulations")
    op.drop_index("ix_economic_simulations_user_id", table_name="economic_simulations")
    op.drop_table("economic_simulations")
    op.drop_table("model_version_datasets")
    op.drop_table("model_versions")
    op.drop_index("ix_fiscal_parameters_parameter_type", table_name="fiscal_parameters")
    op.drop_index("ix_fiscal_parameters_dataset_id", table_name="fiscal_parameters")
    op.drop_table("fiscal_parameters")
    op.drop_index("ix_economic_datasets_name", table_name="economic_datasets")
    op.drop_table("economic_datasets")
