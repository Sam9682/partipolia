"""Schemas of the citizen commitment and profile API."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.economics.decimal_utils import HUNDRED, ZERO
from app.schemas.simulation import SimulationResponse

DurationMonths = Literal[1, 3, 6, 12]

DECLARATION_NOTICE = (
    "Vos engagements sont déclaratifs : ils expriment votre intention et ne sont pas vérifiés. "
    "Aucune transaction ni donnée bancaire n'est collectée."
)


def _check_percentage(value: Decimal) -> Decimal:
    if not value.is_finite() or not (ZERO <= value <= HUNDRED):
        raise ValueError("reduction_percentage must be between 0 and 100")
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < -4:
        raise ValueError("reduction_percentage has too many decimals")
    return value


class CommitmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_code: str = Field(min_length=1, max_length=64)
    reduction_percentage: Decimal
    duration_months: DurationMonths | None = None
    end_date: date | None = Field(default=None, description="Custom duration (end date).")
    start_date: date | None = None

    _pct = field_validator("reduction_percentage")(_check_percentage)

    @model_validator(mode="after")
    def _exactly_one_duration(self) -> CommitmentCreate:
        if (self.duration_months is None) == (self.end_date is None):
            raise ValueError("provide exactly one of duration_months or end_date")
        return self


class CommitmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reduction_percentage: Decimal | None = None
    status: Literal["active", "suspended"] | None = None
    duration_months: DurationMonths | None = None
    end_date: date | None = None
    renew: bool = False

    @field_validator("reduction_percentage")
    @classmethod
    def _pct(cls, value: Decimal | None) -> Decimal | None:
        return None if value is None else _check_percentage(value)

    @model_validator(mode="after")
    def _one_duration_at_most(self) -> CommitmentUpdate:
        if self.duration_months is not None and self.end_date is not None:
            raise ValueError("provide only one of duration_months or end_date")
        return self


class CommitmentOut(BaseModel):
    id: int
    category_code: str
    category_name: str
    reduction_percentage: Decimal
    start_date: date
    end_date: date
    status: str
    is_current: bool = Field(description="Active and within its dates.")
    declared: bool = True  # always a declared commitment, never verified consumption


class MyCommitments(BaseModel):
    commitments: list[CommitmentOut]
    notice: str = DECLARATION_NOTICE
    consent_aggregation: bool
    simulation: SimulationResponse | None = None


class ProfileOut(BaseModel):
    country: str
    region: str | None
    household_adults: int
    consent_aggregation: bool


class CitizenExport(BaseModel):
    """Everything Partipolia holds about a citizen (GDPR right of access/portability)."""

    profile: ProfileOut
    email: str
    pseudonym: str
    commitments: list[CommitmentOut]
    saved_simulations: list[dict[str, object]]
    notice: str = DECLARATION_NOTICE


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, pattern=r"^[0-9A-Z]{2,3}$")
    household_adults: int | None = Field(default=None, ge=1, le=10)
    consent_aggregation: bool | None = None
