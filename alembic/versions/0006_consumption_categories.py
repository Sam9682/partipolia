"""Add ``consumption_categories`` and seed the six reference categories.

Revision ID: 0006_consumption_categories
Revises: 0005_repair_the_law_tables
Create Date: 2026-11-07 00:00:00.000000

Strictly additive. The six reference budgets are the initial configurable
assumptions of the product specification (not official national averages).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal

import sqlalchemy as sa
from alembic import op

revision: str = "0006_consumption_categories"
down_revision: str | None = "0005_repair_the_law_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copy of the seed: a migration must not depend on mutable application code.
_SEED = [
    (
        "restaurants",
        "Restaurants, bars et cafés",
        "Repas au restaurant, bars, cafés, restauration rapide.",
        Decimal("1500"),
    ),
    (
        "clothing",
        "Vêtements et chaussures",
        "Habillement et chaussures pour adultes.",
        Decimal("470"),
    ),
    (
        "leisure",
        "Loisirs et divertissements",
        "Cinéma, spectacles, sorties culturelles et sportives, loisirs.",
        Decimal("600"),
    ),
    (
        "hotels_holidays",
        "Hôtels et vacances",
        "Hébergement touristique, séjours et voyages d'agrément.",
        Decimal("700"),
    ),
    (
        "electronics",
        "Électronique et équipement de la maison",
        "Appareils électroniques, électroménager, équipement du foyer.",
        Decimal("600"),
    ),
    (
        "digital_subscriptions",
        "Abonnements numériques",
        "Streaming, plateformes, applications et services par abonnement.",
        Decimal("300"),
    ),
]


def upgrade() -> None:
    table = op.create_table(
        "consumption_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("reference_annual_expenditure", sa.Numeric(12, 2), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
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
        sa.CheckConstraint(
            "reference_annual_expenditure >= 0",
            name="ck_consumption_categories_expenditure_nonneg",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_consumption_categories_code", "consumption_categories", ["code"], unique=True
    )
    op.bulk_insert(
        table,
        [
            {
                "code": code,
                "name": name,
                "description": desc,
                "reference_annual_expenditure": amount,
                "active": True,
            }
            for code, name, desc, amount in _SEED
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_consumption_categories_code", table_name="consumption_categories")
    op.drop_table("consumption_categories")
