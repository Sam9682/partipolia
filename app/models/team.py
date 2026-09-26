"""Modèles ``Team`` et ``TeamMember`` (tables ``teams`` et ``team_members``).

Exigences :
- 20.1/20.2 : les Équipes regroupent des membres (Utilisateurs) portant un
  ``role`` et une ``bio``.
- 29 (colonnes) : style ``Mapped`` typé de SQLAlchemy 2.x ;
  ``relationship(lazy="selectin")`` pour éviter le problème N+1.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.user import User


class Team(TimestampMixin, Base):
    """Équipe regroupant des membres identifiés (Exigence 20.1)."""

    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    members: Mapped[list["TeamMember"]] = relationship(
        "TeamMember",
        back_populates="team",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Team id={self.id} name={self.name!r}>"


class TeamMember(TimestampMixin, Base):
    """Membre d'une Équipe (Exigence 20.2).

    Porte le ``role`` occupé et une ``bio`` de présentation. Peut être rattaché
    à un Utilisateur existant via ``user_id`` (facultatif, Exigence 1.4) ou
    présenté par ses seuls Champs_Affichage (``display_name``, ``photo_path``,
    ``linkedin_url``) lorsqu'aucun compte n'existe (Exigences 1.1 à 1.3).
    """

    __tablename__ = "team_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # ``user_id`` devient facultatif (Exigence 1.4) ; la contrainte de clé
    # étrangère vers ``users`` est conservée et s'applique aux valeurs non nulles
    # (Exigence 1.5).
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    display_name: Mapped[str | None] = mapped_column(String(255), nullable=True)  # Exigence 1.1
    photo_path: Mapped[str | None] = mapped_column(String(512), nullable=True)  # Exigence 1.2
    linkedin_url: Mapped[str | None] = mapped_column(String(512), nullable=True)  # Exigence 1.3
    role: Mapped[str | None] = mapped_column(String(120), nullable=True)
    bio: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    team: Mapped["Team"] = relationship(
        "Team",
        back_populates="members",
        lazy="selectin",
    )
    user: Mapped["User | None"] = relationship(
        "User",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<TeamMember id={self.id} team_id={self.team_id} user_id={self.user_id}>"
