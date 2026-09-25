"""Ajoute la colonne ``users.consents`` (gestion du consentement RGPD).

Revision ID: 0002_user_consents
Revises: 0001_initial_schema
Create Date: 2024-01-02 00:00:00.000000

Cette migration matérialise la fonction de **gestion du consentement** exigée par
le RGPD (Exigence 28.4). Elle ajoute à la table ``users`` une colonne ``consents``
de type ``jsonb`` (dictionnaire ``{clé: bool}``) avec valeur par défaut ``{}``.

Conformément aux Exigences 28.1 et 28.2, aucune colonne de profilage (profession,
orientation politique, localisation précise) n'est introduite : seul un registre
de choix de consentement fonctionnels est ajouté.

Reproductibilité complète ``alembic upgrade head`` / ``downgrade`` (Exigence 32.4).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# Identifiants de révision, utilisés par Alembic.
revision: str = "0002_user_consents"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Ajoute ``users.consents`` (jsonb, défaut ``{}``, non nul)."""
    op.add_column(
        "users",
        sa.Column(
            "consents",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    """Supprime la colonne ``users.consents``."""
    op.drop_column("users", "consents")
