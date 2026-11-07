"""``CitizenCommitment``: a voluntary, *declared* consumption-reduction commitment.

A commitment is declarative. It is never a verified measure of consumption, and no
transaction or bank data is ever stored.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import CheckConstraint, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin

COMMITMENT_STATUS_VALUES = ("active", "suspended")


class CitizenCommitment(TimestampMixin, Base):
    __tablename__ = "citizen_commitments"
    __table_args__ = (
        UniqueConstraint("user_id", "category_id", name="uq_citizen_commitments_user_category"),
        CheckConstraint(
            "reduction_percentage >= 0 AND reduction_percentage <= 100",
            name="ck_citizen_commitments_percentage",
        ),
        CheckConstraint("end_date > start_date", name="ck_citizen_commitments_dates"),
        CheckConstraint("status IN ('active','suspended')", name="ck_citizen_commitments_status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("consumption_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    reduction_percentage: Mapped[Decimal] = mapped_column(Numeric(7, 4), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
