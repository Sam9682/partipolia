"""Estimation de la taxe foncière sur les loyers perçus (fonction pure)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.net_balance.parameters import PropertyTaxParams


@dataclass(frozen=True, slots=True)
class PropertyTaxResult:
    property_income: Decimal
    rate: Decimal  # ratio appliqué
    tax: Decimal  # money(loyers × taux)
    used_default: bool


def compute_property_tax(
    property_income: Decimal, rate: Decimal | None, params: PropertyTaxParams
) -> PropertyTaxResult:
    """Calcule la taxe foncière estimée ; ``rate=None`` applique le taux par défaut."""
    used_default = rate is None
    applied = params.default_rate if rate is None else rate
    if property_income <= 0:
        return PropertyTaxResult(property_income, applied, ZERO, used_default)
    with localcontext(engine_context()):
        tax = money(property_income * applied)
    return PropertyTaxResult(property_income, applied, tax, used_default)


@dataclass(frozen=True, slots=True)
class RentalIncomeTaxResult:
    property_income: Decimal  # loyers annuels bruts
    rate: Decimal  # taux moyen effectif appliqué (ratio)
    tax: Decimal  # money(loyers × taux)


def compute_rental_income_tax(
    property_income: Decimal, average_rate: Decimal
) -> RentalIncomeTaxResult:
    """Impôt sur les revenus locatifs au taux moyen effectif (un seul arrondi)."""
    if property_income <= 0 or average_rate <= 0:
        return RentalIncomeTaxResult(property_income, average_rate, ZERO)
    with localcontext(engine_context()):
        tax = money(property_income * average_rate)
    return RentalIncomeTaxResult(property_income, average_rate, tax)
