"""Modèle ``User`` (table ``users``).

Exigences 1.1 (email UNIQUE), 1.3 (stockage du seul ``password_hash``, jamais le
mot de passe en clair). Style ``Mapped`` typé de SQLAlchemy 2.x.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.proposal import Proposal


class User(TimestampMixin, Base):
    """Compte utilisateur de la Plateforme.

    ``email`` est UNIQUE (Exigence 1.1). Seul ``password_hash`` est conservé pour
    l'authentification ; le mot de passe en clair n'est jamais persisté (1.3).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Gestion du consentement RGPD (Exigence 28.4). Dictionnaire jsonb
    # ``{clé_de_consentement: bool}`` : la Plateforme n'y stocke que des choix
    # de consentement fonctionnels (aucune donnée de profilage politique,
    # Exigences 28.1, 28.2). ``server_default`` garantit un objet vide côté base.
    consents: Mapped[dict[str, bool]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        server_default="{}",
    )

    # Relations (chargement anticipé selectin pour éviter le problème N+1).
    proposals: Mapped[list["Proposal"]] = relationship(
        "Proposal",
        back_populates="author",
        foreign_keys="Proposal.author_id",
        lazy="selectin",
    )

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return f"<User id={self.id} email={self.email!r}>"
