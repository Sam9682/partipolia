"""Modèles ``Commitment`` et ``Indicator`` (tables ``commitments`` et
``indicators``) — suivi du mandat.

Exigences :
- 21.1/21.2/21.3 : les Engagements (``commitments``) portent un ``status`` contraint
  à ``{NOT_STARTED, IN_PROGRESS, COMPLETED, MODIFIED, ABANDONED}`` (CHECK), une
  ``target_date``, une ``progress`` (avancement) et des ``notes`` ; les Indicateurs
  (``indicators``) portent ``name``, ``unit``, ``baseline``, ``target``,
  ``current_value`` et une source rattachée (``source_id``, Exigence 21.4).
- 29 (colonnes) : style ``Mapped`` typé de SQLAlchemy 2.x ;
  ``relationship(lazy="selectin")`` pour éviter le problème N+1.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, Date, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.source import Source

# Statuts autorisés d'un engagement (Exigence 21.2).
COMMITMENT_STATUSES: tuple[str, ...] = (
    "NOT_STARTED",
    "IN_PROGRESS",
    "COMPLETED",
    "MODIFIED",
    "ABANDONED",
)

_STATUS_CHECK = (
    "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'MODIFIED', 'ABANDONED')"
)


class Commitment(TimestampMixin, Base):
    """Engagement du mandat suivi dans le temps (Exigences 21.1–21.3)."""

    __tablename__ = "commitments"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_commitments_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="NOT_STARTED")
    target_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Commitment id={self.id} title={self.title!r} status={self.status}>"


class Indicator(TimestampMixin, Base):
    """Indicateur de suivi rattaché à une Source (Exigences 21.1, 21.4).

    ``baseline`` (valeur de référence), ``target`` (cible) et ``current_value``
    (valeur courante) sont exprimés dans l'``unit`` déclarée. ``source_id`` référence
    la Source documentaire justifiant les valeurs (FK vers ``sources.id``, créé par
    la tâche parallèle 2.3).
    """

    __tablename__ = "indicators"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    baseline: Mapped[float | None] = mapped_column(Float, nullable=True)
    target: Mapped[float | None] = mapped_column(Float, nullable=True)
    current_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    source_id: Mapped[int | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relation vers la Source (chargement anticipé selectin). Cible référencée par
    # chaîne pour éviter un couplage d'import avec le modèle ``Source`` (tâche 2.3).
    source: Mapped["Source | None"] = relationship(
        "Source",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Indicator id={self.id} name={self.name!r} unit={self.unit!r}>"
