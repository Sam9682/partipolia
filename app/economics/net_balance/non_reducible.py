"""Taxes sur la consommation non réductible (nourriture, électricité, essence).

Module pur, sans I/O.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from app.economics.decimal_utils import ONE, ZERO, money
from app.economics.net_balance.consumption import SCENARIO
from app.economics.parameters import available_modules
from app.economics.types import ModelSnapshot

if TYPE_CHECKING:
    from app.economics.net_balance.parameters import NetBalanceParameterRow


class NonReducibleParameterError(ValueError):
    """Paramètre indispensable au calcul des taxes non réductibles absent ou invalide."""


class Line(StrEnum):
    NOURRITURE = "nourriture"
    ELECTRICITE = "electricite"
    ESSENCE = "essence"


@dataclass(frozen=True, slots=True)
class WeightedRate:
    rate: Decimal  # moyenne pondérée, dans [0, 1]
    rows: tuple[NetBalanceParameterRow, ...]  # lignes réellement utilisées


@dataclass(frozen=True, slots=True)
class NonReducibleParams:
    default_amounts: Mapping[Line, Decimal]
    default_rows: Mapping[Line, NetBalanceParameterRow]
    food_rate: WeightedRate
    electricity_rate: WeightedRate
    fuel_vat_rate: Decimal
    fuel_vat_row: NetBalanceParameterRow
    sources: Mapping[Line, tuple[NetBalanceParameterRow, ...]]


@dataclass(frozen=True, slots=True)
class LineTaxes:
    amount: Decimal
    vat: Decimal
    excise: Decimal  # TICPE, 0 hors essence
    rate: Decimal
    total: Decimal  # vat + excise


@dataclass(frozen=True, slots=True)
class NonReducibleTaxes:
    lines: Mapping[Line, LineTaxes]
    total: Decimal
    used_rows: tuple[NetBalanceParameterRow, ...]
    used_fuel_parameters: tuple[str, ...]  # ex. "fuel_price_ttc_per_l"


_FUEL_PARAMETERS = ("fuel_price_ttc_per_l", "fuel_excise_per_l")


def weighted_rate(rates: Sequence[Decimal], shares: Sequence[Decimal]) -> Decimal:
    """Moyenne pondérée des taux ; les parts doivent être de même nombre et de somme > 0."""
    if len(rates) != len(shares) or not rates:
        raise NonReducibleParameterError("taux et parts de pondération incohérents")
    total_share = sum(shares, ZERO)
    if total_share <= ZERO:
        raise NonReducibleParameterError("la somme des parts de pondération doit être positive")
    return sum((r * s for r, s in zip(rates, shares, strict=True)), ZERO) / total_share


def vat_included(amount: Decimal, rate: Decimal) -> Decimal:
    """TVA contenue dans un montant TTC : montant × taux / (1 + taux), au centime."""
    return money(amount * rate / (ONE + rate))


def _dedupe(
    rows: Sequence[NetBalanceParameterRow],
) -> tuple[NetBalanceParameterRow, ...]:
    seen: set[str] = set()
    result: list[NetBalanceParameterRow] = []
    for row in rows:
        if row.id not in seen:
            seen.add(row.id)
            result.append(row)
    return tuple(result)


def compute_non_reducible_taxes(
    amounts: Mapping[Line, Decimal | None],
    params: NonReducibleParams,
    snapshot: ModelSnapshot,
) -> NonReducibleTaxes:
    """Taxes (TVA, et TICPE pour l'essence) incluses dans la consommation non réductible.

    Un montant absent ou ``None`` applique le défaut du CSV ; ``0`` donne des taxes à 0,00.
    """
    used_rows: list[NetBalanceParameterRow] = []
    used_fuel: list[str] = []
    lines: dict[Line, LineTaxes] = {}

    for line in Line:
        requested = amounts.get(line)
        if requested is None:
            amount = params.default_amounts[line]
            used_rows.append(params.default_rows[line])
        else:
            amount = requested

        excise = ZERO
        if line is Line.NOURRITURE:
            rate = params.food_rate.rate
            rate_rows: Sequence[NetBalanceParameterRow] = params.food_rate.rows
        elif line is Line.ELECTRICITE:
            rate = params.electricity_rate.rate
            rate_rows = params.electricity_rate.rows
        else:
            if not available_modules(snapshot).get("fuel", False):
                raise NonReducibleParameterError("paramètres carburant absents")
            rate = params.fuel_vat_rate
            rate_rows = (params.fuel_vat_row,)
            price = snapshot.param("fuel_price_ttc_per_l").value(SCENARIO)
            if price <= ZERO:
                raise NonReducibleParameterError("prix du carburant invalide")
            if amount > ZERO:
                excise_per_l = snapshot.param("fuel_excise_per_l").value(SCENARIO)
                excise = money(amount / price * excise_per_l)
                used_fuel.extend(_FUEL_PARAMETERS)

        vat = vat_included(amount, rate) if amount > ZERO else ZERO
        if amount > ZERO:
            used_rows.extend(rate_rows)
        lines[line] = LineTaxes(
            amount=amount, vat=vat, excise=excise, rate=rate, total=vat + excise
        )

    return NonReducibleTaxes(
        lines=lines,
        total=sum((t.total for t in lines.values()), ZERO),
        used_rows=_dedupe(used_rows),
        used_fuel_parameters=tuple(dict.fromkeys(used_fuel)),
    )
