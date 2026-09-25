"""Modèle ``Argument`` (table ``arguments``).

Exigence 8.1 : un Argument prend position ``FOR`` ou ``AGAINST`` sur une Proposition
(contrainte CHECK ``position ∈ {FOR, AGAINST}``). Historique en append-only :
seul ``created_at`` est nécessaire (``CreatedAtMixin``).

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")`` avec
cibles référencées par chaîne pour éviter les imports circulaires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal
    from app.models.user import User


# Positions autorisées d'un Argument (Exigence 8.1).
ARGUMENT_POSITIONS: tuple[str, ...] = ("FOR", "AGAINST")


class Argument(CreatedAtMixin, Base):
    """Argument pour ou contre une Proposition (Exigence 8.1).

    ``position`` est borné par CHECK à ``{FOR, AGAINST}``.
    """

    __tablename__ = "arguments"
    __table_args__ = (
        CheckConstraint("position IN ('FOR', 'AGAINST')", name="ck_arguments_position"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        lazy="selectin",
    )
    author: Mapped["User"] = relationship(
        "User",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<Argument id={self.id} proposal_id={self.proposal_id} "
            f"position={self.position}>"
        )
