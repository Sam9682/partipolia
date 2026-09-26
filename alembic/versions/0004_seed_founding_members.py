"""Amorce l'Équipe « Fondateurs » et ses deux Membre_Fondateur.

Revision ID: 0004_seed_founding_members
Revises: 0003_team_member_display_fields
Create Date: 2024-01-04 00:00:00.000000

Cette Migration_Fondateurs insère par instructions SQL Core (indépendantes de
l'ORM) une Équipe « Fondateurs » puis ses deux membres :

- Samuel Lepetre — avec Photo_Statique et URL LinkedIn (Exigence 3.2) ;
- Nael Lepetre — sans photo ni LinkedIn (Exigence 3.3) ;
- les deux avec ``user_id`` nul (Exigence 3.4).

L'``upgrade`` crée l'Équipe (Exigence 3.1) et les deux membres. Le ``downgrade``
supprime d'abord les deux membres, ciblés par ``display_name`` **et** par
appartenance à l'Équipe « Fondateurs », puis supprime l'Équipe, restituant
l'état antérieur (Exigence 3.5).

Reproductibilité complète ``alembic upgrade head`` / ``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# Identifiants de révision, utilisés par Alembic.
revision: str = "0004_seed_founding_members"
down_revision: str | None = "0003_team_member_display_fields"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Constantes partagées entre ``upgrade`` et ``downgrade`` pour un ciblage exact
# au rollback (Exigence 3.5).
_TEAM_NAME = "Fondateurs"
_PHOTO_PATH = "img/team/samuel-lepetre.jpg"  # relatif au montage StaticFiles
_LINKEDIN_SAMUEL = "https://www.linkedin.com/in/samuel-lepetre%F0%9F%8F%89-90010713/"
_MEMBER_NAMES = ("Samuel Lepetre", "Nael Lepetre")


def upgrade() -> None:
    """Crée l'Équipe « Fondateurs » et insère ses deux membres."""
    conn = op.get_bind()
    team_id = conn.execute(
        sa.text(
            "INSERT INTO teams (name, created_at, updated_at) "
            "VALUES (:name, now(), now()) RETURNING id"
        ),
        {"name": _TEAM_NAME},
    ).scalar_one()

    conn.execute(
        sa.text(
            "INSERT INTO team_members "
            "(team_id, user_id, display_name, photo_path, linkedin_url, created_at, updated_at) "
            "VALUES "
            "(:tid, NULL, :n1, :p1, :l1, now(), now()), "
            "(:tid, NULL, :n2, NULL, NULL, now(), now())"
        ),
        {
            "tid": team_id,
            "n1": _MEMBER_NAMES[0],
            "p1": _PHOTO_PATH,
            "l1": _LINKEDIN_SAMUEL,
            "n2": _MEMBER_NAMES[1],
        },
    )


def downgrade() -> None:
    """Supprime les deux membres puis l'Équipe « Fondateurs »."""
    conn = op.get_bind()
    conn.execute(
        sa.text(
            "DELETE FROM team_members WHERE display_name IN :names AND team_id IN "
            "(SELECT id FROM teams WHERE name = :team)"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"names": list(_MEMBER_NAMES), "team": _TEAM_NAME},
    )
    conn.execute(sa.text("DELETE FROM teams WHERE name = :team"), {"team": _TEAM_NAME})
