"""Schemas of the national dashboard (aggregated, privacy-preserving)."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.schemas.simulation import FiscalResult, MoneyRange, SectorImpact


class DashboardNational(BaseModel):
    """National aggregate of declared commitments.

    Below ``min_cohort`` participants every figure is withheld (``suppressed``) so
    that nobody can be re-identified. Amounts are modelled estimates, not measured
    tax revenue, and commitments are declared, not verified.
    """

    currency: str = "EUR"
    period: str = "projection annuelle"
    suppressed: bool
    min_cohort: int
    participants_label: str
    participants: int | None = None
    active_commitments: int | None = None
    consumption_reduction: Decimal | None = None
    personal_savings: Decimal | None = None
    fiscal: FiscalResult | None = None
    net_public_impact_monthly: MoneyRange | None = None
    net_public_impact_daily: MoneyRange | None = None
    calculated_at: datetime | None = None
    model_version: str | None = None
    dataset_versions: list[str] = []
    confidence: str | None = None
    methodology: str
    disclaimers: list[str]


class HistoryPoint(BaseModel):
    date: date
    participants: int
    net_public_impact: Decimal
    personal_savings: Decimal


class DashboardHistory(BaseModel):
    min_cohort: int
    points: list[HistoryPoint]


class DashboardSectors(BaseModel):
    suppressed: bool
    min_cohort: int
    sectors: list[SectorImpact]
    unit: str = "EUR par an (turnover et valeur ajoutée réduits)"
