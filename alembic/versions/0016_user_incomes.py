"""Create user_incomes (statut and net income per account).

Nouvelle table uniquement : aucune donnée existante n'est modifiée et aucun
remplissage rétroactif n'est fait (ces valeurs n'ont jamais été stockées).

Revision ID: 0016_user_incomes
Revises: 0015_net_balance_verdicts
Create Date: 2026-11-14 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0016_user_incomes"
down_revision: str | None = "0015_net_balance_verdicts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_incomes",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("statut", sa.String(length=20), nullable=False),
        sa.Column("revenu_net", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("user_id", name="pk_user_incomes"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_user_incomes_user_id_users",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "statut IN ('salarie','sans_emploi','fonctionnaire','entrepreneur')",
            name="ck_user_incomes_statut",
        ),
    )


def downgrade() -> None:
    op.drop_table("user_incomes")
