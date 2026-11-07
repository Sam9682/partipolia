"""Fuel and transport impact of avoided trips.

Each avoided purchase may have avoided a trip. Per category::

    trips = avoided TTC / 1000 x trips per 1000 EUR
    km = trips x kilometres per trip x car share
    litres = km / 100 x litres per 100 km
    fuel spend avoided (TTC) = litres x pump price
    excise avoided = litres x excise per litre
    VAT avoided on fuel = fuel spend x r / (1 + r)

Avoided trips are attributed to exactly one category (the one that generated them)
through that category's own parameters, so the same fuel is never counted twice.
Fuel savings are reported separately and are NOT added to personal savings.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.direct import vat_from_ttc
from app.economics.types import ModelSnapshot

THOUSAND = Decimal(1000)
HUNDRED = Decimal(100)


@dataclass(frozen=True, slots=True)
class FuelImpact:
    litres: Decimal
    household_fuel_savings: Decimal  # fuel spend avoided by households (TTC)
    excise: Decimal
    vat: Decimal
    litres_by_category: Mapping[str, Decimal]

    @property
    def tax_revenue_loss(self) -> Decimal:
        return self.excise + self.vat


def compute_fuel_impact(
    snapshot: ModelSnapshot, avoided_ttc_by_category: Mapping[str, Decimal], scenario: str
) -> FuelImpact:
    l_per_100 = snapshot.param("fuel_l_per_100km").value(scenario)
    price = snapshot.param("fuel_price_ttc_per_l").value(scenario)
    excise_per_l = snapshot.param("fuel_excise_per_l").value(scenario)
    vat_rate = snapshot.param("fuel_vat_rate").value(scenario)

    with localcontext(engine_context()):
        litres_total = ZERO
        by_category: dict[str, Decimal] = {}
        for category, avoided in avoided_ttc_by_category.items():
            trips = (
                avoided
                / THOUSAND
                * snapshot.param("trips_per_1000_eur", category=category).value(scenario)
            )
            km = (
                trips
                * snapshot.param("km_per_trip", category=category).value(scenario)
                * snapshot.param("car_share", category=category).value(scenario)
            )
            litres = km / HUNDRED * l_per_100
            by_category[category] = litres
            litres_total += litres
        spend = litres_total * price
        return FuelImpact(
            litres=litres_total.quantize(Decimal("0.001")),
            household_fuel_savings=money(spend),
            excise=money(litres_total * excise_per_l),
            vat=money(vat_from_ttc(spend, vat_rate)),
            litres_by_category={k: v.quantize(Decimal("0.001")) for k, v in by_category.items()},
        )
