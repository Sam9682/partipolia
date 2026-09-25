"""Modèle ``Comment`` (table ``comments``).

Exigences :
- 9.2 : ``parent_id`` auto-référence pour les fils de discussion (réponses en
  arborescence).
- 9.4 : ``status`` par défaut ``VISIBLE`` à la publication (contrainte CHECK sur les
  statuts autorisés).

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


# Statuts autorisés d'un Commentaire (Exigence 9.4 : VISIBLE par défaut ;
# HIDDEN/PENDING utilisés par la modération, Exigences 17.x).
COMMENT_STATUSES: tuple[str, ...] = ("VISIBLE", "HIDDEN", "PENDING")

_STATUS_CHECK = "status IN ('VISIBLE', 'HIDDEN', 'PENDING')"


class Comment(CreatedAtMixin, Base):
    """Commentaire sur une Proposition, éventuellement en réponse à un autre.

    ``parent_id`` référence un Commentaire de la même table (fil de discussion,
    Exigence 9.2). ``status`` vaut ``VISIBLE`` par défaut à la publication
    (Exigence 9.4).
    """

    __tablename__ = "comments"
    __table_args__ = (
        CheckConstraint(_STATUS_CHECK, name="ck_comments_status"),
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
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="VISIBLE")

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposal: Mapped["Proposal"] = relationship(
        "Proposal",
        lazy="selectin",
    )
    author: Mapped["User"] = relationship(
        "User",
        lazy="selectin",
    )
    parent: Mapped["Comment | None"] = relationship(
        "Comment",
        back_populates="replies",
        remote_side="Comment.id",
        lazy="selectin",
    )
    replies: Mapped[list["Comment"]] = relationship(
        "Comment",
        back_populates="parent",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<Comment id={self.id} proposal_id={self.proposal_id} "
            f"parent_id={self.parent_id} status={self.status}>"
        )
