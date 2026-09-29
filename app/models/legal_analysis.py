"""Modèle ``LegalAnalysis`` (table ``legal_analyses``) — « Réparer la loi ».

Matérialisation des analyses IA (Exigences 3, 5, 6, 7, 12.6).

Les analyses IA sont des générations **coûteuses** et **rarement changeantes** : elles
sont persistées ici (source de vérité, auditable, ré-affichable sans coût), la restitution
SSR/API lisant cette table sans jamais générer en ligne.

Exigences :
- 3.1 / 5.1 / 6.1 / 7.1 : matérialisation de l'Analyse_Juridique, de la Simulation, de la
  Détection_D_Effets_Pervers et des Agents_IA_Contradictoires par réforme (``proposal_id``).
- 12.6 : chaque ligne stocke le résultat RAG (``answer``, ``payload.sources``, ``confidence``),
  l'agent (``kind``), la version documentaire (``doc_version``) et un ``status``, ce qui rend
  les analyses auditables.

Contraintes :
- ``UNIQUE(proposal_id, kind)`` : une analyse courante par réforme et par agent
  (régénération par UPSERT).
- ``kind`` borné par CHECK aux 9 Agents_IA_Contradictoires.
- ``status`` borné par CHECK à ``{PENDING, READY, INDISPONIBLE}``.
- ``confidence`` borné par CHECK à ``[0, 1]``.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal


class LegalAnalysis(TimestampMixin, Base):
    """Analyse IA matérialisée pour une réforme et un agent (Exigences 3, 5, 6, 7, 12.6)."""

    __tablename__ = "legal_analyses"
    __table_args__ = (
        UniqueConstraint("proposal_id", "kind", name="uq_legal_analyses_proposal_kind"),
        CheckConstraint(
            "kind IN ('ANALYSE_JURIDIQUE','SIMULATION','EFFETS_PERVERS',"
            "'JURISTE','BUDGET','CONSTITUTION','IMPACT','OPPOSANT','DEFENSEUR')",
            name="ck_legal_analyses_kind",
        ),
        CheckConstraint(
            "status IN ('PENDING','READY','INDISPONIBLE')",
            name="ck_legal_analyses_status",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_legal_analyses_confidence_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    # payload structuré : sources[] {number, chunk_id, source_id, document_id, url},
    # markers[] (fait/estimation/opinion/hypothèse/désaccord), horizons (simulation),
    # risks[] (effets pervers).
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # empreinte du corpus
    doc_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    proposal: Mapped[Proposal] = relationship(lazy="selectin")

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<LegalAnalysis id={self.id} proposal_id={self.proposal_id} "
            f"kind={self.kind!r} status={self.status!r}>"
        )
