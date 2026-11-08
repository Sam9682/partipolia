"""``UserIncome``: dernier statut et revenu net annuel saisis par un compte.

Minimisation RGPD : seuls le statut et le revenu net sont conservés. Aucun
impôt, aide ou donnée immobilière n'est stocké ; l'impôt est recalculé côté
serveur à la demande. La ligne est supprimée avec le compte (CASCADE).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

# Doit rester aligné sur ``app.economics.net_balance.types.Status``.
INCOME_STATUS_VALUES = ("salarie", "sans_emploi", "fonctionnaire", "entrepreneur")


class UserIncome(Base):
    """Une ligne de revenus par compte (clé primaire = ``user_id``)."""

    __tablename__ = "user_incomes"
    __table_args__ = (
        CheckConstraint(
            "statut IN ('salarie','sans_emploi','fonctionnaire','entrepreneur')",
            name="ck_user_incomes_statut",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    statut: Mapped[str] = mapped_column(String(20), nullable=False)
    revenu_net: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
