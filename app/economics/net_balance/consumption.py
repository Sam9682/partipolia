"""Consumption contribution (VAT and fuel taxes) of the household.

The existing engine computes VAT and fuel taxes on *avoided* amounts. To obtain
those of the *remaining* expenditure without touching engine functions, the
reductions are inverted (``100 - r``): the "avoided" amount of that simulation
is exactly the household's remaining expenditure.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.economics.decimal_utils import HUNDRED, ZERO
from app.economics.direct import compute_direct_impact
from app.economics.fuel import compute_fuel_impact
from app.economics.parameters import available_modules
from app.economics.personal import compute_personal_savings
from app.economics.types import ModelSnapshot

SCENARIO = "central"


@dataclass(frozen=True, slots=True)
class ConsumptionContribution:
    vat: Decimal
    fuel_excise: Decimal
    fuel_vat: Decimal
    total: Decimal  # vat + fuel_excise + fuel_vat


def compute_consumption_contribution(
    snapshot: ModelSnapshot,
    reductions: Mapping[str, Decimal],
    adults: int,
    reference_overrides: Mapping[str, Decimal] | None,
) -> ConsumptionContribution:
    """VAT and fuel taxes paid on the household's remaining expenditure."""
    # Unknown codes are kept so compute_personal_savings still raises KeyError.
    codes = set(snapshot.category_codes) | set(reductions)
    inverse = {code: HUNDRED - reductions.get(code, ZERO) for code in codes}
    remaining = compute_personal_savings(snapshot, inverse, adults, reference_overrides)
    direct = compute_direct_impact(snapshot, remaining, SCENARIO)

    fuel_excise = ZERO
    fuel_vat = ZERO
    if available_modules(snapshot).get("fuel", False):
        fuel = compute_fuel_impact(
            snapshot,
            {c.code: c.avoided_expenditure for c in remaining.categories},
            SCENARIO,
        )
        fuel_excise = fuel.excise
        fuel_vat = fuel.vat

    return ConsumptionContribution(
        vat=direct.vat,
        fuel_excise=fuel_excise,
        fuel_vat=fuel_vat,
        total=direct.vat + fuel_excise + fuel_vat,
    )
