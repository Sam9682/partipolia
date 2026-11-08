"""Schemas of the citizen commitment and profile API."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.economics.decimal_utils import HUNDRED, ZERO
from app.reference.regions import is_valid_region
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


PlanOutcome = Literal["created", "updated", "unchanged"]


class PlanItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_code: str = Field(min_length=1, max_length=64)
    reduction_percentage: Decimal = Field(description="0 removes the commitment.")

    _pct = field_validator("reduction_percentage")(_check_percentage)


class CommitmentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PlanItem] = Field(min_length=1)
    duration_months: int = Field(default=3, ge=1, le=36)

    @model_validator(mode="after")
    def _unique_categories(self) -> CommitmentPlan:
        codes = [item.category_code for item in self.items]
        if len(codes) != len(set(codes)):
            raise ValueError("category codes must be unique within a plan")
        return self


def plan_outcome(created: int, updated: int, deleted: int) -> PlanOutcome:
    """Message kind derived from the plan counters."""
    if created > 0:
        return "created"
    if updated > 0 or deleted > 0:
        return "updated"
    return "unchanged"


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


class CommitmentPlanResult(BaseModel):
    outcome: PlanOutcome
    created: int
    updated: int
    deleted: int
    commitments: list[CommitmentOut]


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


class NetBalanceVerdictOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    verdict: str
    balance: Decimal
    computed_at: datetime


class CitizenExport(BaseModel):
    """Everything Partipolia holds about a citizen (GDPR right of access/portability)."""

    profile: ProfileOut
    email: str
    pseudonym: str
    commitments: list[CommitmentOut]
    saved_simulations: list[dict[str, object]]
    net_balance_verdict: NetBalanceVerdictOut | None = None
    notice: str = DECLARATION_NOTICE


class ProfileUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    region: str | None = Field(default=None, pattern=r"^[0-9A-Z]{2,3}$")
    household_adults: int | None = Field(default=None, ge=1, le=10)
    consent_aggregation: bool | None = None

    @field_validator("region")
    @classmethod
    def _region_in_reference_list(cls, value: str | None) -> str | None:
        if value is not None and not is_valid_region(value):
            raise ValueError("region must be a code of the versioned Region list")
        return value
