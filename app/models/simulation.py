"""Saved economic simulations and their per-category results.

Only the *input* (reduction percentages, household size) and the model version are
stored: no consumption transaction and no bank data. Results are reproducible by
re-running the engine on the referenced, immutable model version.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

SCENARIO_TYPES = ("personal", "conservative", "central", "high_impact")


class EconomicSimulation(Base):
    __tablename__ = "economic_simulations"
    __table_args__ = (
        CheckConstraint(
            "scenario_type IN ('personal','conservative','central','high_impact')",
            name="ck_economic_simulations_scenario",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    scenario_type: Mapped[str] = mapped_column(String(16), nullable=False, default="personal")
    input_payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list[SimulationResult]] = relationship(
        "SimulationResult",
        back_populates="simulation",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class SimulationResult(Base):
    __tablename__ = "simulation_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    simulation_id: Mapped[int] = mapped_column(
        ForeignKey("economic_simulations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int] = mapped_column(
        ForeignKey("consumption_categories.id", ondelete="RESTRICT"), nullable=False
    )
    personal_savings: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    direct_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    indirect_fiscal_impact: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    induced_fiscal_impact: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    additional_public_expenditure: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, default=0
    )
    net_fiscal_impact: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    confidence_low: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    confidence_high: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    simulation: Mapped[EconomicSimulation] = relationship(
        "EconomicSimulation", back_populates="results"
    )
