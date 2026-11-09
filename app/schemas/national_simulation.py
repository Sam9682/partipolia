"""Pydantic schemas of the national scenario simulator API.

Rates and percentages are ``Decimal`` values in [0, 100] with at most 2 decimals;
``NaN`` / ``Infinity`` and non numeric strings are rejected. Validation errors
name the faulty field (``participation_rate`` or ``reductions.<code>``). All
monetary amounts are ``Decimal`` values serialized as strings in JSON.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.economics.categories import CATEGORY_CODES
from app.economics.decimal_utils import ZERO
from app.schemas.simulation import FiscalResult

#: A percentage in [0, 100], finite, with at most 2 decimals.
Percentage = Annotated[
    Decimal,
    Field(ge=0, le=100, decimal_places=2, allow_inf_nan=False),
]

Horizon = Literal["short", "medium", "long"]

MAX_REDUCTIONS = len(CATEGORY_CODES)
MIN_COMPARED_SCENARIOS = 2
MAX_COMPARED_SCENARIOS = 4

_KNOWN_CATEGORIES = frozenset(CATEGORY_CODES)


class NationalScenarioRequest(BaseModel):
    """Input of ``POST /api/v1/national-simulations/calculate`` (a hypothesis, not a forecast)."""

    model_config = ConfigDict(extra="forbid")

    participation_rate: Percentage = Field(
        description="Share of the reference population that takes part (0-100, 2 decimals)."
    )
    reductions: dict[str, Percentage] = Field(
        description="Reduction percentage (0-100, 2 decimals) per category code."
    )
    horizon: Horizon = Field(
        default="medium", description="Time horizon of employment adjustments."
    )

    @field_validator("reductions")
    @classmethod
    def _check_reductions(cls, value: dict[str, Decimal]) -> dict[str, Decimal]:
        for code in value:
            if code not in _KNOWN_CATEGORIES:
                raise ValueError(f"reductions.{code}: unknown category")
        if not any(percentage > ZERO for percentage in value.values()):
            raise ValueError("reductions: at least one reduction must be strictly positive")
        return value


class NationalCompareRequest(BaseModel):
    """Input of ``POST /api/v1/national-simulations/compare``."""

    model_config = ConfigDict(extra="forbid")

    scenarios: list[NationalScenarioRequest]

    @field_validator("scenarios")
    @classmethod
    def _check_count(cls, value: list[NationalScenarioRequest]) -> list[NationalScenarioRequest]:
        if not MIN_COMPARED_SCENARIOS <= len(value) <= MAX_COMPARED_SCENARIOS:
            raise ValueError("scenarios: 2 à 4 scénarios")
        return value


class ScenarioEcho(BaseModel):
    """Echo of the scenario parameters used for a computation."""

    participation_rate: Decimal
    reductions: dict[str, Decimal]
    horizon: Horizon


class PopulationInfo(BaseModel):
    """Reference population with its provenance."""

    value: Decimal
    source: str
    as_of: date | None = None
    confidence_label: Literal["official", "provisional", "low"]


class ExtrapolatedLine(BaseModel):
    """One extrapolated line (charge, tax or aid) of the scenario, in euros."""

    code: str
    label: str
    kind: Literal["charge", "tax", "aid"]
    amount: Decimal
    provisional: bool
    provisional_parameters: list[str] = Field(default_factory=list)


class ExtrapolationBlock(BaseModel):
    """Extrapolated charges, taxes and aids with totals."""

    lines: list[ExtrapolatedLine]
    total_levies: Decimal
    total_aids: Decimal
    balance: Decimal  # extrapolated levies minus extrapolated aids


class NationalScenarioResult(BaseModel):
    """Output of the national simulator. A hypothesis, not a forecast."""

    scenario: ScenarioEcho
    model_version: str
    dataset_versions: list[str]
    population: PopulationInfo
    participants: Decimal
    avoided_by_category: dict[str, Decimal]
    fiscal: FiscalResult
    confidence: str
    confidence_warning: str | None = None
    provisional_parameters: list[str] = Field(default_factory=list)
    disclaimers: list[str]
    extrapolation: ExtrapolationBlock | None = None
    extrapolation_message: str | None = None


class NationalComparison(BaseModel):
    """Side-by-side results of 2 to 4 scenarios, in the order requested."""

    results: list[NationalScenarioResult]


class MobilisationGap(BaseModel):
    """Gap between a scenario rate and the declared mobilisation rate.

    ``declared_rate``, ``gap_pp`` and ``participants`` are only set when
    ``status == "ok"``; they are never ``0`` as a placeholder.
    """

    status: Literal["ok", "unavailable", "suppressed", "inconsistent"]
    scenario_rate: Decimal | None = None
    declared_rate: Decimal | None = None
    gap_pp: Decimal | None = None
    participants: int | None = None
    message: str | None = None
    calculated_at: datetime | None = None
    model_version: str | None = None


class ScenarioPreset(BaseModel):
    """A ready-made scenario offered to visitors."""

    label: str = Field(min_length=1, max_length=80)
    description: str
    participation_rate: Percentage
    reductions: dict[str, Percentage]
    horizon: Horizon = "medium"


class SavedScenarioCreate(NationalScenarioRequest):
    """Input of ``POST /national-simulations/saved``.

    ``name`` is validated by the service (1..100 characters => 422) so that the
    ownership check keeps priority on routes that address an existing scenario.
    """

    name: str


class SavedScenarioRename(BaseModel):
    """Input of ``PATCH /national-simulations/saved/{id}`` (name checked by the service)."""

    model_config = ConfigDict(extra="forbid")

    name: str


class SavedScenarioSummaryOut(BaseModel):
    """A saved scenario in a list: name, date and central net impact."""

    id: int
    name: str
    created_at: datetime
    net_impact_central: Decimal
    model_version: str


class ShareLinkOut(BaseModel):
    """Share link; the raw token is only ever returned here, once."""

    url: str
    token: str
