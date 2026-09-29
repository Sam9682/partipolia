"""Modèle ``LegalProblem`` (table ``legal_problems``) — « Réparer la loi ».

Exigences :
- 1.1 / 2.1 : un Problème_Juridique décrit un dysfonctionnement légal (title, summary,
  textes de loi concernés et références de jurisprudence en listes JSONB).
- 2.2 / 2.3 : ``complexity_level`` borné par CHECK à ``{FAIBLE, MOYEN, ELEVE}``.
- 2.4 : ``theme_id`` NOT NULL, rattache le problème à exactement un Thème
  (``ondelete=RESTRICT`` : un Thème référencé ne peut être supprimé).
- 2.6 : ``slug`` UNIQUE (index) et ``id`` unique attribués à la création.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.legal_problem_reform import LegalProblemReform


class LegalProblem(TimestampMixin, Base):
    """Problème juridique à réparer (Exigences 1, 2)."""

    __tablename__ = "legal_problems"
    __table_args__ = (
        CheckConstraint(
            "complexity_level IN ('FAIBLE', 'MOYEN', 'ELEVE')",
            name="ck_legal_problems_complexity_level",
        ),
        CheckConstraint(
            "affected_citizens_count >= 0",
            name="ck_legal_problems_affected_nonneg",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    affected_citizens_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    concerned_legal_texts: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    jurisprudence_refs: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    complexity_level: Mapped[str] = mapped_column(String(16), nullable=False)  # FAIBLE|MOYEN|ELEVE
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="PUBLISHED")

    # Réformes proposées rattachées (association ``legal_problem_reforms``).
    # ``LegalProblemReform`` n'existe pas encore : référence différée par chaîne.
    reform_links: Mapped[list[LegalProblemReform]] = relationship(
        "LegalProblemReform",
        back_populates="problem",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<LegalProblem id={self.id} slug={self.slug!r} title={self.title!r}>"
