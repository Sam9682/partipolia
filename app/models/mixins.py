"""Mixins partagés pour les modèles ORM (style ``Mapped`` typé, SQLAlchemy 2.x).

``TimestampMixin`` fournit les colonnes ``created_at`` / ``updated_at`` renseignées
côté base (``server_default`` / ``onupdate``) afin de garantir des horodatages
cohérents quel que soit le point d'écriture (API, Services, Worker Celery).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column


class TimestampMixin:
    """Ajoute ``created_at`` et ``updated_at`` gérés par la base de données."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    """Ajoute uniquement ``created_at`` (entités en append-only, ex. historiques)."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
