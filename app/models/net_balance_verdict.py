"""``NetBalanceVerdict``: dernier verdict du bilan net d'un compte.

Seuls le verdict, le montant du bilan et la date de calcul sont conservés.
Aucune autre donnée saisie n'est stockée ici ; le statut et le revenu net
saisis sont conservés séparément dans ``UserIncome`` (``user_incomes``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

VERDICT_VALUES = ("contributeur_net", "beneficiaire_net", "solde_nul")


class NetBalanceVerdict(Base):
    """Un verdict par compte (clé primaire = ``user_id``)."""

    __tablename__ = "user_net_balance_verdicts"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('contributeur_net','beneficiaire_net','solde_nul')",
            name="ck_user_net_balance_verdicts_verdict",
        ),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    balance: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
