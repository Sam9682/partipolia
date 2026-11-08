"""Energy baseline line: average yearly fuel and heating-oil spend per adult.

Per energy (fuel, heating oil)::

    spend (TTC) = litres x pump price
    excise = litres x excise per litre
    VAT = spend x r / (1 + r)

NOTE (task 1.1): ``energy_baseline`` is deliberately NOT an entry of
``MODULE_REQUIREMENTS``; otherwise ``available_modules``, ``fiscal.modules`` and
``_used_confidence`` would change for existing results. Parameters are read
directly from the snapshot; ``parameters.py`` exposes
``ENERGY_BASELINE_REQUIREMENTS`` and ``energy_baseline_available(snapshot)``.

This module never calls ``compute_fuel_impact``: the baseline is a stock
(average consumption), not an avoided-trips flow.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import engine_context, money
from app.economics.direct import vat_from_ttc
from app.economics.types import ModelSnapshot

_LITRES_QUANTUM = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class EnergyLine:
    spend_ttc: Decimal
    excise: Decimal
    vat: Decimal
    litres: Decimal


@dataclass(frozen=True, slots=True)
class EnergyBaseline:
    fuel: EnergyLine
    heating_oil: EnergyLine
    total: EnergyLine


class EnergyConfigurationError(ValueError):
    """A required parameter is absent; the message names the parameter."""


def _value(snapshot: ModelSnapshot, parameter_type: str, scenario: str) -> Decimal:
    try:
        return snapshot.param(parameter_type).value(scenario)
    except KeyError:
        raise EnergyConfigurationError(
            f"missing energy baseline parameter: {parameter_type}"
        ) from None


def _line(litres: Decimal, price: Decimal, excise_per_l: Decimal, vat_rate: Decimal) -> EnergyLine:
    spend = litres * price
    return EnergyLine(
        spend_ttc=money(spend),
        excise=money(litres * excise_per_l),
        vat=money(vat_from_ttc(spend, vat_rate)),
        litres=litres.quantize(_LITRES_QUANTUM),
    )


def compute_energy_baseline(snapshot: ModelSnapshot, scenario: str = "central") -> EnergyBaseline:
    """Average annual energy spend, excise and VAT per adult."""
    fuel_l = _value(snapshot, "avg_fuel_l_per_adult", scenario)
    oil_l = _value(snapshot, "avg_heating_oil_l_per_adult", scenario)
    fuel_price = _value(snapshot, "fuel_price_ttc_per_l", scenario)
    oil_price = _value(snapshot, "heating_oil_price_ttc_per_l", scenario)
    fuel_excise = _value(snapshot, "fuel_excise_per_l", scenario)
    oil_excise = _value(snapshot, "heating_oil_excise_per_l", scenario)
    vat_rate = _value(snapshot, "fuel_vat_rate", scenario)

    with localcontext(engine_context()):
        fuel = _line(fuel_l, fuel_price, fuel_excise, vat_rate)
        oil = _line(oil_l, oil_price, oil_excise, vat_rate)
        total = EnergyLine(
            spend_ttc=fuel.spend_ttc + oil.spend_ttc,
            excise=fuel.excise + oil.excise,
            vat=fuel.vat + oil.vat,
            litres=fuel.litres + oil.litres,
        )
    return EnergyBaseline(fuel=fuel, heating_oil=oil, total=total)
