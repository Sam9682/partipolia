"""Fiscal parameter rows, validation and snapshot construction (pure, no database).

Datasets are stored as flat parameter rows (low / central / high). A published
model version is the union of the rows of its datasets; :func:`build_snapshot`
turns those rows into the immutable :class:`ModelSnapshot` consumed by the engine.

Convention for the three value columns: ``low`` is the parameter value giving the
**low-impact** scenario and ``high`` the high-impact one. For most parameters this
is numerically increasing; for offsetting parameters (e.g. household re-spending)
it is the reverse, which is why those types are declared ``ordered=False``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.economics.decimal_utils import ONE, ZERO
from app.economics.types import (
    SCENARIOS,
    CategoryParameters,
    Confidence,
    ModelSnapshot,
    ParameterKey,
    ParameterRange,
    VatComponent,
    VatProfile,
)

_CONFIDENCE_ORDER = (Confidence.HIGH, Confidence.MEDIUM, Confidence.LOW, Confidence.PROVISIONAL)


@dataclass(frozen=True, slots=True)
class ParameterTypeSpec:
    """Validation rules of a parameter type."""

    category_scoped: bool
    ordered: bool  # require low <= central <= high


_NATIONAL = ParameterTypeSpec(category_scoped=False, ordered=True)
_NATIONAL_UNORDERED = ParameterTypeSpec(category_scoped=False, ordered=False)
_CATEGORY = ParameterTypeSpec(category_scoped=True, ordered=True)
_CATEGORY_SHARE = ParameterTypeSpec(category_scoped=True, ordered=False)

#: Known parameter types. The engine modules read these by name.
PARAMETER_TYPES: dict[str, ParameterTypeSpec] = {
    # Final-expenditure splits: shares sum to 1 per category and scenario.
    "vat_split": _CATEGORY_SHARE,  # key = VAT rate
    "demand_share": _CATEGORY_SHARE,  # key = A38 product receiving the spending
    # Input-output tables and branch accounts (INSEE, M EUR).
    "io_output": _NATIONAL,
    "io_ci_total": _NATIONAL,
    "io_flow": _NATIONAL,  # key = "product>branch"
    "io_import_share": _NATIONAL,
    "branch_value_added": _NATIONAL,
    "branch_compensation": _NATIONAL,
    "branch_wages": _NATIONAL,
    "branch_ebe": _NATIONAL,
    # Fiscal parameters.
    "corporate_tax_rate": _NATIONAL,
    "ebe_to_taxable_profit": _NATIONAL,
    "production_tax_rate_on_va": _NATIONAL,
    # Employment (key = "<horizon>:<branch or *>" or "<horizon>").
    "employment_elasticity": _NATIONAL,
    "job_loss_share": _NATIONAL,
    "employee_contribution_rate": _NATIONAL,
    "income_tax_rate_on_wages": _NATIONAL,
    "unemployment_replacement_rate": _NATIONAL,
    "unemployment_months": _NATIONAL,
    "partial_activity_cost_rate": _NATIONAL,
    # Offsetting effects: "low" (low impact) is the LARGER offset.
    "respend_share": _NATIONAL_UNORDERED,
    "respend_vat_rate": _NATIONAL_UNORDERED,
    # Fuel and transport.
    "fuel_l_per_100km": _NATIONAL,
    "fuel_price_ttc_per_l": _NATIONAL,
    "fuel_excise_per_l": _NATIONAL,
    "fuel_vat_rate": _NATIONAL,
    "car_share": _CATEGORY,
    "km_per_trip": _CATEGORY,
    "trips_per_1000_eur": _CATEGORY,
}

#: Types whose shares must add up to exactly 1 for each category and scenario.
SUM_TO_ONE_TYPES = frozenset({"vat_split", "demand_share"})

#: Parameter types a model must contain to be published (minimum viable model).
REQUIRED_FOR_PUBLICATION = ("vat_split",)

#: Module capabilities: which parameter types each engine module needs.
MODULE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "indirect": (
        "demand_share",
        "io_output",
        "io_flow",
        "io_import_share",
        "branch_value_added",
        "branch_ebe",
        "corporate_tax_rate",
        "ebe_to_taxable_profit",
        "production_tax_rate_on_va",
    ),
    "induced": (
        "branch_wages",
        "branch_compensation",
        "employment_elasticity",
        "job_loss_share",
        "employee_contribution_rate",
        "income_tax_rate_on_wages",
        "unemployment_replacement_rate",
        "unemployment_months",
        "partial_activity_cost_rate",
    ),
    "fuel": (
        "car_share",
        "km_per_trip",
        "trips_per_1000_eur",
        "fuel_l_per_100km",
        "fuel_price_ttc_per_l",
        "fuel_excise_per_l",
        "fuel_vat_rate",
    ),
    "rebound": ("respend_share", "respend_vat_rate"),
}


@dataclass(frozen=True, slots=True)
class ParameterRow:
    category_code: str | None
    parameter_type: str
    key: str
    low: Decimal
    central: Decimal
    high: Decimal
    unit: str
    confidence: Confidence
    note: str = ""

    def value(self, scenario: str) -> Decimal:
        return {"low": self.low, "central": self.central, "high": self.high}[scenario]


def parse_decimal(text: str) -> Decimal:
    """Parse a finite decimal ("," accepted as separator); raises ``ValueError``."""
    try:
        value = Decimal(text.strip().replace(",", "."))
    except InvalidOperation as exc:
        raise ValueError(f"not a number: {text!r}") from exc
    if not value.is_finite():
        raise ValueError(f"not a finite number: {text!r}")
    return value


def validate_rows(rows: Iterable[ParameterRow]) -> list[str]:
    """Return human-readable validation errors (empty list = valid)."""
    errors: list[str] = []
    seen: set[tuple[str | None, str, str]] = set()
    shares: dict[tuple[str, str, str], Decimal] = defaultdict(lambda: ZERO)

    for index, row in enumerate(rows, start=1):
        where = f"row {index} ({row.category_code or '-'}/{row.parameter_type}/{row.key})"
        spec = PARAMETER_TYPES.get(row.parameter_type)
        if spec is None:
            errors.append(f"{where}: unknown parameter type")
            continue
        if spec.category_scoped and not row.category_code:
            errors.append(f"{where}: category_code is required")
        if not spec.category_scoped and row.category_code:
            errors.append(f"{where}: category_code must be empty")
        identity = (row.category_code, row.parameter_type, row.key)
        if identity in seen:
            errors.append(f"{where}: duplicate parameter")
        seen.add(identity)
        if spec.ordered and not (row.low <= row.central <= row.high):
            errors.append(f"{where}: expected low <= central <= high")
        if row.parameter_type == "vat_split":
            try:
                rate = parse_decimal(row.key)
            except ValueError:
                errors.append(f"{where}: key must be a VAT rate such as 0.10")
                continue
            if not (ZERO <= rate < ONE):
                errors.append(f"{where}: VAT rate must be in [0, 1)")
        if row.parameter_type in SUM_TO_ONE_TYPES:
            for scenario in SCENARIOS:
                share = row.value(scenario)
                if not (ZERO <= share <= ONE):
                    errors.append(f"{where}: {scenario} share must be in [0, 1]")
                if row.category_code:
                    shares[(row.parameter_type, row.category_code, scenario)] += share

    for (ptype, category, scenario), total in sorted(shares.items()):
        if total != ONE:
            errors.append(f"{category}: {scenario} {ptype} sum to {total}, expected 1")
    return errors


def _lowest_confidence(values: Sequence[Confidence]) -> Confidence:
    return max(values, key=_CONFIDENCE_ORDER.index) if values else Confidence.PROVISIONAL


def build_snapshot(
    model_version: str,
    dataset_versions: Sequence[str],
    categories: Sequence[CategoryParameters],
    rows: Sequence[ParameterRow],
) -> ModelSnapshot:
    """Build an immutable snapshot; raises ``ValueError`` if the rows are invalid."""
    errors = validate_rows(rows)
    if errors:
        raise ValueError("; ".join(errors[:5]))

    known = {c.code for c in categories}
    unknown = {r.category_code for r in rows if r.category_code} - known
    if unknown:
        raise ValueError(f"unknown category in parameters: {sorted(unknown)[0]}")

    by_category: dict[str, list[ParameterRow]] = defaultdict(list)
    parameters: dict[ParameterKey, ParameterRange] = {}
    for row in rows:
        parameters[(row.parameter_type, row.category_code, row.key)] = ParameterRange(
            low=row.low,
            central=row.central,
            high=row.high,
            confidence=row.confidence,
            unit=row.unit,
            note=row.note,
        )
        if row.parameter_type == "vat_split" and row.category_code:
            by_category[row.category_code].append(row)

    profiles: dict[str, VatProfile] = {}
    for code, category_rows in by_category.items():
        splits = {
            scenario: tuple(
                VatComponent(rate=parse_decimal(r.key), share=r.value(scenario))
                for r in sorted(category_rows, key=lambda r: parse_decimal(r.key))
                if r.value(scenario) > ZERO
            )
            for scenario in SCENARIOS
        }
        profiles[code] = VatProfile(
            splits=splits,
            confidence=_lowest_confidence([r.confidence for r in category_rows]),
            source=" | ".join(sorted({r.note for r in category_rows if r.note})),
        )

    return ModelSnapshot(
        model_version=model_version,
        dataset_versions=tuple(dataset_versions),
        categories=tuple(categories),
        vat_profiles=profiles,
        parameters=parameters,
    )


def missing_parameters(
    categories: Sequence[CategoryParameters], rows: Sequence[ParameterRow]
) -> list[str]:
    """List parameters required for publication that are absent from ``rows``."""
    with_vat = {r.category_code for r in rows if r.parameter_type == "vat_split"}
    return [f"vat_split for {c.code}" for c in categories if c.code not in with_vat]


def available_modules(snapshot: ModelSnapshot) -> dict[str, bool]:
    """Which optional engine modules the snapshot has complete data for."""
    result = {}
    for module, types in MODULE_REQUIREMENTS.items():
        result[module] = snapshot.has(*types) and _category_coverage(snapshot, module)
    return result


def _category_coverage(snapshot: ModelSnapshot, module: str) -> bool:
    """Category-scoped parameters must exist for every category."""
    for ptype in MODULE_REQUIREMENTS[module]:
        if PARAMETER_TYPES[ptype].category_scoped:
            for category in snapshot.category_codes:
                if not snapshot.keyed(ptype, category):
                    return False
    return True
