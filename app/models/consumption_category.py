"""``ConsumptionCategory`` model (table ``consumption_categories``).

The reference annual expenditure is a configurable assumption (not an official
national average) editable by administrators.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import Boolean, CheckConstraint, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin


class ConsumptionCategory(TimestampMixin, Base):
    __tablename__ = "consumption_categories"
    __table_args__ = (
        CheckConstraint(
            "reference_annual_expenditure >= 0", name="ck_consumption_categories_expenditure_nonneg"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    reference_annual_expenditure: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ConsumptionCategory {self.code!r}>"
