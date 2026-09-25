"""Modèles ``Program`` et ``ProgramProposal`` (tables ``programs`` et
``program_proposals``).

Exigences :
- 18.1/18.2/18.4 : le Programme regroupe des Propositions via une **association**
  ``program_proposals`` porteuse d'attributs propres : ``priority`` (priorité
  d'inclusion) et ``included_at`` (horodatage d'inclusion définitive, explicite et
  traçable). L'inclusion n'est jamais décidée par l'IA.
- 29 (colonnes) : style ``Mapped`` typé de SQLAlchemy 2.x ;
  ``relationship(lazy="selectin")`` pour éviter le problème N+1.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal


class Program(CreatedAtMixin, Base):
    """Programme politique agrégeant des Propositions incluses (Exigence 18.1)."""

    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")

    # Association ordonnée par priorité (chargement anticipé selectin).
    program_proposals: Mapped[list["ProgramProposal"]] = relationship(
        "ProgramProposal",
        back_populates="program",
        cascade="all, delete-orphan",
        order_by="ProgramProposal.priority",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Program id={self.id} status={self.status}>"


class ProgramProposal(Base):
    """Association ``programs`` ↔ ``proposals`` (Exigences 18.2, 18.4).

    Porte les attributs propres à l'inclusion d'une Proposition dans un Programme :
    ``priority`` (ordre/priorité) et ``included_at`` (horodatage d'inclusion
    définitive, explicite et traçable). L'inclusion est un acte humain, jamais
    produit par l'IA.
    """

    __tablename__ = "program_proposals"

    id: Mapped[int] = mapped_column(primary_key=True)
    program_id: Mapped[int] = mapped_column(
        ForeignKey("programs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    included_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    program: Mapped["Program"] = relationship(
        "Program",
        back_populates="program_proposals",
        lazy="selectin",
    )
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<ProgramProposal program_id={self.program_id} "
            f"proposal_id={self.proposal_id} priority={self.priority}>"
        )
