"""``AggregatedImpact``: privacy-preserving snapshots of the collective impact.

Only aggregates are stored here, never anything about an individual participant.
``details`` keeps the low / central / high ranges and the sector breakdown.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Date, DateTime, Index, Integer, Numeric, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class AggregatedImpact(Base):
    __tablename__ = "aggregated_impacts"
    __table_args__ = (
        Index(
            "uq_aggregated_impacts_snapshot",
            "aggregation_period",
            "geographical_level",
            "geographical_code",
            "snapshot_date",
            unique=True,
        ),
        Index(
            "ix_aggregated_impacts_level_code_date",
            "geographical_level",
            "geographical_code",
            text("snapshot_date DESC"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    aggregation_period: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    geographical_level: Mapped[str] = mapped_column(String(16), nullable=False, default="national")
    geographical_code: Mapped[str] = mapped_column(String(8), nullable=False, default="FR")
    participant_count: Mapped[int] = mapped_column(Integer, nullable=False)
    active_commitments: Mapped[int] = mapped_column(Integer, nullable=False)
    consumption_reduction: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    personal_savings: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    direct_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    indirect_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    induced_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    net_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)
    snapshot_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        default=lambda: datetime.now(UTC).date(),
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    calculated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
