"""Projection of a scenario's fiscal components onto the eight tax types.

Pure module: no database or web access. ``ScenarioOutcome.components`` already holds
seven of the eight levies; property tax is obtained by splitting ``production_tax``
with an optional share, so that ``taxe_fonciere + impots_production`` equals
``production_tax`` exactly. Public expenditure components (unemployment benefits,
partial activity) and the rebound offset are not levies and stay out of the split.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from app.economics.decimal_utils import ZERO, money
from app.economics.engine import ScenarioOutcome

#: Fixed order and French labels of the tax types.
TAX_TYPES: tuple[tuple[str, str], ...] = (
    ("tva", "TVA"),
    ("accises", "Accises"),
    ("charges_salariales", "Charges salariales"),
    ("charges_patronales", "Charges patronales"),
    ("impot_revenu", "Impôt sur le revenu"),
    ("taxe_fonciere", "Taxe foncière"),
    ("impots_production", "Impôts de production"),
    ("impot_societes", "Impôt sur les sociétés"),
)

#: Optional parameter type (key ``"*"``) giving the property-tax share of production taxes.
PROPERTY_TAX_SHARE = "property_tax_share_of_production_tax"


@dataclass(frozen=True, slots=True)
class TaxAmounts:
    """Amounts of one scenario per tax type, with an availability flag per type."""

    amounts: Mapping[str, Decimal]
    available: Mapping[str, bool]


def project_scenario(outcome: ScenarioOutcome, property_share: Decimal | None) -> TaxAmounts:
    """Project ``outcome.components`` onto the eight tax types.

    A type whose module (or optional parameter) is missing gets a zero amount and
    ``available=False``; the other types are still computed.
    """
    c = outcome.components
    has_fuel = outcome.fuel is not None
    has_indirect = outcome.indirect is not None
    has_employment = outcome.employment is not None

    production = money(c.get("production_tax", ZERO))
    if property_share is None:
        property_tax = ZERO
    else:
        property_tax = money(production * property_share)
    other_production = production - property_tax

    amounts = {
        "tva": money(c.get("direct_vat", ZERO) + c.get("fuel_vat", ZERO)),
        "accises": money(c.get("fuel_excise", ZERO)),
        "charges_salariales": money(c.get("employee_contributions", ZERO)),
        "charges_patronales": money(c.get("employer_contributions", ZERO)),
        "impot_revenu": money(c.get("income_tax", ZERO)),
        "taxe_fonciere": property_tax,
        "impots_production": other_production,
        "impot_societes": money(c.get("corporate_tax", ZERO)),
    }
    available = {
        "tva": True,
        "accises": has_fuel,
        "charges_salariales": has_employment,
        "charges_patronales": has_employment,
        "impot_revenu": has_employment,
        "taxe_fonciere": has_indirect and property_share is not None,
        "impots_production": has_indirect,
        "impot_societes": has_indirect,
    }
    return TaxAmounts(amounts=amounts, available=available)
