"""Pydantic schemas of the personal simulator API.

All monetary amounts are ``Decimal`` values serialized as strings in JSON, so no
floating point value is ever exchanged for money.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.economics.decimal_utils import HUNDRED, ZERO


class SimulationRequest(BaseModel):
    """Input of ``POST /api/v1/simulations/calculate``."""

    model_config = ConfigDict(extra="forbid")

    reductions: dict[str, Decimal] = Field(
        default_factory=dict,
        description="Reduction percentage (0-100) per category code.",
    )
    adults: int = Field(default=1, ge=1, le=10, description="Number of adults in the household.")
    horizon: Literal["short", "medium", "long"] = Field(
        default="medium", description="Time horizon of employment adjustments."
    )
    save: bool = Field(
        default=False,
        description="Store the input (never any personal data) so the result can be re-read.",
    )

    @field_validator("reductions")
    @classmethod
    def _check_reductions(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        if len(value) > 50:
            raise ValueError("too many categories")
        for code, percentage in value.items():
            if not percentage.is_finite() or not (ZERO <= percentage <= HUNDRED):
                raise ValueError(f"reduction for {code!r} must be between 0 and 100")
            if percentage.as_tuple().exponent < -4:  # type: ignore[operator]
                raise ValueError(f"reduction for {code!r} has too many decimals")
        return value


class MoneyRange(BaseModel):
    """A low / central / high estimate."""

    low: Decimal
    central: Decimal
    high: Decimal


class CategoryResult(BaseModel):
    code: str
    name: str
    reduction_percentage: Decimal
    original_expenditure: Decimal
    avoided_expenditure: Decimal
    remaining_expenditure: Decimal
    annual_savings: Decimal
    direct_vat: MoneyRange
    turnover_ht: MoneyRange
    vat_confidence: str
    # Exact per-category contributions (the model is linear, so they add up).
    indirect: MoneyRange | None = None
    induced: MoneyRange | None = None
    additional_public_expenditure: MoneyRange | None = None
    net_public_impact: MoneyRange | None = None


class PersonalResult(BaseModel):
    adults: int
    original_expenditure: Decimal
    avoided_expenditure: Decimal
    remaining_expenditure: Decimal
    annual_savings: Decimal
    monthly_savings: Decimal


class DirectFiscalResult(BaseModel):
    """Direct fiscal impact (VAT on avoided final expenditure)."""

    currency: str = "EUR"
    period: str = "année"
    vat: MoneyRange
    turnover_ht: MoneyRange
    confidence: str
    methodology: str


class PublicSectorBreakdown(BaseModel):
    """Simplified split between public sub-sectors (central, local, social security)."""

    central: MoneyRange
    local: MoneyRange
    social_security: MoneyRange


class FiscalResult(BaseModel):
    """Full fiscal estimate. Every figure is a low / central / high range in EUR per year.

    Positive numbers are revenue losses or extra spending; ``rebound_offset`` is a
    recovery. These are MODELLED ESTIMATES, not measured tax revenue.
    """

    currency: str = "EUR"
    period: str = "année"
    horizon: str
    modules: dict[str, bool]
    direct: MoneyRange  # VAT on final goods + fuel taxes
    indirect: MoneyRange | None  # production + corporate tax along the supplier chain
    induced: MoneyRange | None  # employment-related revenue
    gross_revenue_loss: MoneyRange
    additional_public_expenditure: MoneyRange
    rebound_offset: MoneyRange
    net_public_impact: MoneyRange
    # Other, distinct indicators (not tax revenue):
    reduced_turnover: MoneyRange | None  # supplier-chain turnover
    reduced_value_added: MoneyRange | None
    household_fuel_savings: MoneyRange | None
    import_leakage: MoneyRange | None
    by_public_sector: PublicSectorBreakdown
    top_sectors: list[SectorImpact]
    confidence: str
    provisional_parameters: list[str]
    methodology: str


class SectorImpact(BaseModel):
    code: str
    turnover_change: Decimal
    value_added_change: Decimal


class SimulationResponse(BaseModel):
    """Output of the simulator. Indicators are intentionally kept separate."""

    simulation_id: int | None = None
    model_version: str
    dataset_versions: list[str]
    personal: PersonalResult
    direct: DirectFiscalResult
    fiscal: FiscalResult
    categories: list[CategoryResult]
    disclaimers: list[str]
