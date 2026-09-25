"""Modèle ``Vote`` (table ``votes``).

Exigences :
- 6.2 : **UNIQUE(proposal_id, user_id)** — un Utilisateur ne peut porter qu'un seul
  Vote par Proposition (UPSERT à la revote).
- 8.1/9.2 (participation) : ``value ∈ {-1, 0, +1}`` (contrainte CHECK) où ``+1``
  exprime le soutien, ``-1`` l'opposition et ``0`` le vote NEUTRE.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")`` avec
cibles référencées par chaîne pour éviter les imports circulaires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal
    from app.models.user import User


class Vote(TimestampMixin, Base):
    """Vote à trois valeurs porté par un Utilisateur sur une Proposition.

    L'unicité ``(proposal_id, user_id)`` (Exigence 6.2) garantit un seul Vote par
    couple ; la contrainte CHECK borne ``value`` à ``{-1, 0, +1}``.
    """

    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("proposal_id", "user_id", name="uq_votes_proposal_user"),
        CheckConstraint("value IN (-1, 0, 1)", name="ck_votes_value"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    value: Mapped[int] = mapped_column(Integer, nullable=False)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        lazy="selectin",
    )
    user: Mapped["User"] = relationship(
        "User",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<Vote id={self.id} proposal_id={self.proposal_id} "
            f"user_id={self.user_id} value={self.value}>"
        )
