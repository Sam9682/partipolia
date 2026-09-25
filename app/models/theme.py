"""Modèle ``Theme`` (table ``themes``).

Exigences 2.1 (25 Thèmes amorcés) et 2.5 (``proposals.theme_id`` NOT NULL, côté
proposition). ``slug`` est UNIQUE. Style ``Mapped`` typé de SQLAlchemy 2.x.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal


class Theme(Base):
    """Thème thématique regroupant des propositions (Exigence 2.1)."""

    __tablename__ = "themes"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposals: Mapped[list["Proposal"]] = relationship(
        "Proposal",
        back_populates="theme",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Theme id={self.id} slug={self.slug!r}>"
