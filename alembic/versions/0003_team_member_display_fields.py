"""Ajoute les Champs_Affichage à ``team_members`` et rend ``user_id`` nullable.

Revision ID: 0003_team_member_display_fields
Revises: 0002_user_consents
Create Date: 2024-01-03 00:00:00.000000

Cette migration étend la table ``team_members`` pour permettre des
Membre_Fondateur sans compte Utilisateur :

- ajoute ``display_name`` (String 255) — Exigence 1.1 ;
- ajoute ``photo_path`` (String 512) — Exigence 1.2 ;
- ajoute ``linkedin_url`` (String 512) — Exigence 1.3 ;
- rend ``user_id`` nullable — Exigence 1.4.

La contrainte de clé étrangère ``team_members.user_id`` → ``users.id`` n'est pas
touchée : rendre la colonne nullable ne supprime pas la FK, qui continue de
s'appliquer aux valeurs non nulles (Exigence 1.5).

Reproductibilité complète ``alembic upgrade head`` / ``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Identifiants de révision, utilisés par Alembic.
revision: str = "0003_team_member_display_fields"
down_revision: str | None = "0002_user_consents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Ajoute les Champs_Affichage et rend ``user_id`` nullable."""
    op.add_column("team_members", sa.Column("display_name", sa.String(255), nullable=True))
    op.add_column("team_members", sa.Column("photo_path", sa.String(512), nullable=True))
    op.add_column("team_members", sa.Column("linkedin_url", sa.String(512), nullable=True))
    op.alter_column("team_members", "user_id", existing_type=sa.Integer(), nullable=True)


def downgrade() -> None:
    """Restaure ``user_id`` NON NULL puis retire les Champs_Affichage."""
    op.alter_column("team_members", "user_id", existing_type=sa.Integer(), nullable=False)
    op.drop_column("team_members", "linkedin_url")
    op.drop_column("team_members", "photo_path")
    op.drop_column("team_members", "display_name")
