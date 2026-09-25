"""Modèles ``Source`` et ``ProposalSource`` (tables ``sources`` et
``proposal_sources``).

Exigences :
- 10.1 : Source documentaire (title, url, publisher, publication_date, is_verified).
- 10.2 : ``source_type`` contraint aux 7 valeurs
  ``{OFFICIAL, ACADEMIC, STATISTICAL, MEDIA, REPORT, LEGISLATION, OTHER}`` (CHECK).
- 10.3 : association ``proposal_sources`` porte ``relevance_score``.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Float,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.document import Document
    from app.models.proposal import Proposal


# Types de source autorisés (Exigence 10.2).
SOURCE_TYPES: tuple[str, ...] = (
    "OFFICIAL",
    "ACADEMIC",
    "STATISTICAL",
    "MEDIA",
    "REPORT",
    "LEGISLATION",
    "OTHER",
)

_SOURCE_TYPE_CHECK = (
    "source_type IN ('OFFICIAL', 'ACADEMIC', 'STATISTICAL', 'MEDIA', "
    "'REPORT', 'LEGISLATION', 'OTHER')"
)


class Source(TimestampMixin, Base):
    """Source documentaire de référence (Exigences 10.1, 10.2)."""

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(_SOURCE_TYPE_CHECK, name="ck_sources_source_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    publisher: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="OTHER")
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal_links: Mapped[list["ProposalSource"]] = relationship(
        "ProposalSource",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    documents: Mapped[list["Document"]] = relationship(
        "Document",
        back_populates="source",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Source id={self.id} type={self.source_type} title={self.title!r}>"


class ProposalSource(CreatedAtMixin, Base):
    """Association Proposition ↔ Source portant un ``relevance_score`` (Exigence 10.3)."""

    __tablename__ = "proposal_sources"
    __table_args__ = (
        CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 1",
            name="ck_proposal_sources_relevance_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        lazy="selectin",
    )
    source: Mapped["Source"] = relationship(
        "Source",
        back_populates="proposal_links",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<ProposalSource proposal_id={self.proposal_id} "
            f"source_id={self.source_id} relevance={self.relevance_score}>"
        )
