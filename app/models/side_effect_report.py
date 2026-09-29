"""Modèle ``SideEffectReport`` (table ``side_effect_reports``) — « Réparer la loi ».

Exigences :
- 8.8 : un Signalement_D_Effet_Secondaire est rattaché à la Réforme (``Proposal``)
  et à son auteur (``User``).
- 8.9 : la longueur du contenu est validée en amont (Pydantic, 1..5000) et garantie
  par CHECK (``char_length(content) <= 5000``).
- 15 (modération) : ``status`` vaut ``PENDING`` par défaut, puis ``VISIBLE`` si
  « acceptable » ou ``REJECTED`` si « interdit » (contrainte CHECK sur les statuts).

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")`` avec
cibles référencées par chaîne pour éviter les imports circulaires.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal
    from app.models.user import User


# Statuts autorisés d'un Signalement (Exigence 15 : PENDING par défaut ;
# VISIBLE/REJECTED décidés par la modération).
SIDE_EFFECT_REPORT_STATUSES: tuple[str, ...] = ("PENDING", "VISIBLE", "REJECTED")

_STATUS_CHECK = "status IN ('PENDING', 'VISIBLE', 'REJECTED')"


class SideEffectReport(TimestampMixin, Base):
    """Signalement d'un effet secondaire sur une Réforme (Exigences 8.8, 8.9)."""

    __tablename__ = "side_effect_reports"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_side_effect_reports_status"),
        CheckConstraint(
            "char_length(content) <= 5000",
            name="ck_side_effect_reports_len",
        ),
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
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped[Proposal] = relationship(
        "Proposal",
        lazy="selectin",
    )
    author: Mapped[User] = relationship(
        "User",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<SideEffectReport id={self.id} proposal_id={self.proposal_id} "
            f"author_id={self.author_id} status={self.status}>"
        )
