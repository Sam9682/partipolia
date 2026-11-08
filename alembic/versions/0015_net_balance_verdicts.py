"""Create user_net_balance_verdicts (latest net balance verdict per account).

Nouvelle table uniquement : aucune donnée existante n'est modifiée et aucun
remplissage rétroactif n'est fait (un verdict ne peut pas être déduit de
données qui n'ont jamais été stockées).

Revision ID: 0015_net_balance_verdicts
Revises: 0014_consultations
Create Date: 2026-11-13 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_net_balance_verdicts"
down_revision: str | None = "0014_consultations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_net_balance_verdicts",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("balance", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_net_balance_verdicts"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_net_balance_verdicts_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "verdict IN ('contributeur_net','beneficiaire_net','solde_nul')",
            name="ck_user_net_balance_verdicts_verdict",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_net_balance_verdicts")
