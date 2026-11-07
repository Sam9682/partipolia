"""Immutable data types of the economic engine.

The engine is a pure function of a :class:`ModelSnapshot` (the published model and
dataset versions) and a user input. Nothing here depends on the web or database
layers, which keeps results deterministic and reproducible.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Confidence(StrEnum):
    """Confidence label attached to a parameter or a result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    PROVISIONAL = "provisional"


@dataclass(frozen=True, slots=True)
class CategoryParameters:
    """Parameters of one consumption category within a model snapshot."""

    code: str
    name: str
    reference_annual_expenditure: Decimal  # EUR per adult per year, TTC


SCENARIOS: tuple[str, str, str] = ("low", "central", "high")


@dataclass(frozen=True, slots=True)
class VatComponent:
    """One VAT rate applying to a share of a category's TTC expenditure."""

    rate: Decimal  # fraction, e.g. Decimal("0.10")
    share: Decimal  # fraction of the category's TTC expenditure, in (0, 1]


@dataclass(frozen=True, slots=True)
class VatProfile:
    """VAT split of a category, for each scenario (low / central / high).

    ``low`` is the split yielding the lowest VAT, ``high`` the highest. Each split
    must have shares summing exactly to 1. ``confidence`` and ``source`` document
    where the split comes from (rates are legal; shares are often assumptions).
    """

    splits: Mapping[str, tuple[VatComponent, ...]]
    confidence: Confidence
    source: str

    def __post_init__(self) -> None:
        if set(self.splits) != set(SCENARIOS):
            raise ValueError("a VatProfile needs exactly the low, central and high splits")
        for name, components in self.splits.items():
            if not components:
                raise ValueError(f"empty VAT split: {name}")
            if sum((c.share for c in components), Decimal(0)) != Decimal(1):
                raise ValueError(f"VAT shares must sum to 1 ({name})")
            for component in components:
                if not (Decimal(0) <= component.rate < Decimal(1)):
                    raise ValueError("VAT rate must be a fraction in [0, 1)")
                if not (Decimal(0) < component.share <= Decimal(1)):
                    raise ValueError("VAT share must be in (0, 1]")


@dataclass(frozen=True, slots=True)
class ParameterRange:
    """A parameter value for the three scenarios.

    ``low`` / ``central`` / ``high`` are ordered by their *effect*: the ``low``
    column always yields the low-impact (conservative) scenario, even when its
    numeric value is the largest (e.g. a household re-spending share).
    """

    low: Decimal
    central: Decimal
    high: Decimal
    confidence: Confidence
    unit: str = ""
    note: str = ""

    def value(self, scenario: str) -> Decimal:
        return {"low": self.low, "central": self.central, "high": self.high}[scenario]


#: (parameter_type, category_code or None, key)
ParameterKey = tuple[str, str | None, str]


@dataclass(frozen=True, slots=True)
class ModelSnapshot:
    """Immutable snapshot of a published model version and its datasets."""

    model_version: str
    dataset_versions: tuple[str, ...]
    categories: tuple[CategoryParameters, ...]
    vat_profiles: Mapping[str, VatProfile] = field(default_factory=dict)
    parameters: Mapping[ParameterKey, ParameterRange] = field(default_factory=dict)

    def param(
        self, parameter_type: str, key: str = "*", category: str | None = None
    ) -> ParameterRange:
        """Return one parameter; ``KeyError`` if the model does not define it."""
        return self.parameters[(parameter_type, category, key)]

    def keyed(self, parameter_type: str, category: str | None = None) -> dict[str, ParameterRange]:
        """All parameters of a type (optionally of one category), by key."""
        return {
            k[2]: v
            for k, v in self.parameters.items()
            if k[0] == parameter_type and k[1] == category
        }

    def has(self, *parameter_types: str) -> bool:
        present = {k[0] for k in self.parameters}
        return all(t in present for t in parameter_types)

    def category(self, code: str) -> CategoryParameters:
        for category in self.categories:
            if category.code == code:
                return category
        raise KeyError(code)

    @property
    def category_codes(self) -> tuple[str, ...]:
        return tuple(c.code for c in self.categories)


@dataclass(frozen=True, slots=True)
class CategorySavings:
    """Personal figures for one category (all amounts in EUR per year, TTC)."""

    code: str
    reduction_percentage: Decimal
    original_expenditure: Decimal
    avoided_expenditure: Decimal  # reference x reduction (the amount not spent)
    remaining_expenditure: Decimal
    annual_savings: Decimal  # personal savings: equals avoided_expenditure


@dataclass(frozen=True, slots=True)
class PersonalSavings:
    """Personal savings across all categories."""

    adults: int
    categories: tuple[CategorySavings, ...]
    original_expenditure: Decimal
    avoided_expenditure: Decimal
    remaining_expenditure: Decimal
    annual_savings: Decimal
    monthly_savings: Decimal
