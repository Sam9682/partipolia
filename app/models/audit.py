"""Modèle ``AuditLog`` (table ``audit_logs``) — journal d'audit inaltérable.

Exigences :
- 29.1 : consignation des actions sensibles (``action``, ``entity_type``,
  ``entity_id``, états ``old_data`` / ``new_data`` en jsonb).
- 29.2 : **``ip_hash`` uniquement** — l'adresse IP brute n'est JAMAIS stockée ;
  seule sa forme hachée est conservée à des fins de corrélation.

Table en **append-only** : on n'utilise que ``created_at`` (aucun ``updated_at``),
les enregistrements d'audit ne doivent pas être modifiés après écriture.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import CreatedAtMixin


class AuditLog(CreatedAtMixin, Base):
    """Entrée immuable du journal d'audit (Exigences 29.1, 29.2).

    ``old_data`` et ``new_data`` capturent l'état avant/après d'une entité lors
    d'une action sensible. ``ip_hash`` conserve uniquement l'empreinte hachée de
    l'adresse IP de l'auteur de l'action — jamais l'IP en clair (Exigence 29.2).
    """

    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    entity_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Empreinte hachée de l'IP — jamais l'adresse brute (Exigence 29.2).
    ip_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - aide au débogage
        return (
            f"<AuditLog id={self.id} action={self.action!r} "
            f"entity={self.entity_type}#{self.entity_id}>"
        )
