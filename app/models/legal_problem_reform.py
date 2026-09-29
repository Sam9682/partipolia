"""Modèle d'association ``LegalProblemReform`` (table ``legal_problem_reforms``).

Rattache une Réforme_Proposée (``Proposal``) à un ``LegalProblem`` — « Réparer la loi ».

Décision de conception : une table d'association (option B de la conception) plutôt
qu'une colonne sur ``Proposal``. Elle est la moins intrusive — ``Proposal`` reste
inchangé (Exigence 13.1) — et le lien ainsi que l'attribut ``is_status_quo`` vivent
dans la table de jointure.

Exigences :
- 2.5 / 4.1 : rattachement Réforme ↔ Problème via l'association.
- 4.3 : marqueur ``is_status_quo`` porté par le lien (au plus un statu quo par problème).
- 13.1 : aucune colonne ajoutée à ``Proposal``.

Contraintes :
- ``UniqueConstraint("problem_id", "proposal_id")`` (``uq_lpr_problem_proposal``) : une
  même Réforme n'est rattachée qu'une fois à un Problème donné.
- Index partiel ``UNIQUE(problem_id) WHERE is_status_quo`` (``uq_lpr_one_status_quo``) :
  au plus un statu quo par Problème (défense en profondeur ; la cardinalité complète
  3..5 dont 1 statu quo est validée par ``legal_reform_service``).
- ``ondelete=CASCADE`` sur ``problem_id`` (le lien disparaît avec le Problème) et
  ``ondelete=RESTRICT`` sur ``proposal_id`` (impossible de supprimer une ``Proposal``
  encore rattachée).

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CreatedAtMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.legal_problem import LegalProblem
    from app.models.proposal import Proposal


class LegalProblemReform(CreatedAtMixin, Base):
    """Lien Réforme_Proposée ↔ Problème_Juridique (Exigences 2.5, 4.1, 4.3, 13.1)."""

    __tablename__ = "legal_problem_reforms"
    __table_args__ = (
        UniqueConstraint("problem_id", "proposal_id", name="uq_lpr_problem_proposal"),
        Index(
            "uq_lpr_one_status_quo",
            "problem_id",
            unique=True,
            postgresql_where=text("is_status_quo"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    problem_id: Mapped[int] = mapped_column(
        ForeignKey("legal_problems.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    is_status_quo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    problem: Mapped[LegalProblem] = relationship(
        back_populates="reform_links",
        lazy="selectin",
    )
    proposal: Mapped[Proposal] = relationship(lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<LegalProblemReform id={self.id} problem_id={self.problem_id} "
            f"proposal_id={self.proposal_id} is_status_quo={self.is_status_quo}>"
        )
