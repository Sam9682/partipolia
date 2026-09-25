"""Modèles ``Proposal`` et ``ProposalVersion`` (tables ``proposals`` et
``proposal_versions``).

Exigences :
- 3.1/3.2 : création à l'état DRAFT, version initiale 1, ``slug`` attribué.
- 3.3 : ``status ∈ {DRAFT, PENDING_REVIEW, PUBLISHED, ARCHIVED, REJECTED}`` (CHECK),
  ``slug`` UNIQUE, ``theme_id`` NOT NULL (Exigence 2.5), ``version ≥ 1`` (CHECK).
- 3.4/3.5 : historique complet et immuable dans ``proposal_versions`` (``snapshot``
  jsonb + ``change_summary``), jamais écrasé.

Style ``Mapped`` typé de SQLAlchemy 2.x ; ``relationship(lazy="selectin")``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.theme import Theme
    from app.models.user import User


# Statuts autorisés d'une proposition (Exigence 3.3).
PROPOSAL_STATUSES: tuple[str, ...] = (
    "DRAFT",
    "PENDING_REVIEW",
    "PUBLISHED",
    "ARCHIVED",
    "REJECTED",
)

_STATUS_CHECK = "status IN ('DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'ARCHIVED', 'REJECTED')"


class Proposal(TimestampMixin, Base):
    """Proposition citoyenne rattachée à un Thème (Exigences 3.1–3.5)."""

    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_proposals_status"),
        CheckConstraint("version >= 1", name="ck_proposals_version_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    theme_id: Mapped[int] = mapped_column(
        ForeignKey("themes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    slug: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    problem: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    expected_impact: Mapped[str | None] = mapped_column(Text, nullable=True)
    implementation_delay: Mapped[str | None] = mapped_column(String(120), nullable=True)
    estimated_cost: Mapped[Any | None] = mapped_column(Numeric(18, 2), nullable=True)
    estimated_savings: Mapped[Any | None] = mapped_column(Numeric(18, 2), nullable=True)
    estimated_revenue: Mapped[Any | None] = mapped_column(Numeric(18, 2), nullable=True)
    funding_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    legal_constraints: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="DRAFT")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    theme: Mapped["Theme"] = relationship(
        "Theme",
        back_populates="proposals",
        lazy="selectin",
    )
    author: Mapped["User"] = relationship(
        "User",
        back_populates="proposals",
        foreign_keys=[author_id],
        lazy="selectin",
    )
    versions: Mapped[list["ProposalVersion"]] = relationship(
        "ProposalVersion",
        back_populates="proposal",
        cascade="all, delete-orphan",
        order_by="ProposalVersion.version",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<Proposal id={self.id} slug={self.slug!r} status={self.status} v{self.version}>"


class ProposalVersion(CreatedAtMixin, Base):
    """Instantané immuable d'une proposition à une version donnée (Exigences 3.4, 3.5).

    Chaque modification d'une proposition crée une nouvelle ligne avec le
    ``snapshot`` complet (jsonb) et un ``change_summary`` ; les versions
    antérieures ne sont jamais écrasées.
    """

    __tablename__ = "proposal_versions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_proposal_versions_version_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    proposal_id: Mapped[int] = mapped_column(
        ForeignKey("proposals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    edited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        back_populates="versions",
        lazy="selectin",
    )
    editor: Mapped["User | None"] = relationship(
        "User",
        foreign_keys=[edited_by],
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<ProposalVersion id={self.id} proposal_id={self.proposal_id} v{self.version}>"
