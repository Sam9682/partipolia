"""Impôt sur le revenu : barème progressif et quotient familial (pur, en ``Decimal``)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.net_balance.parameters import IncomeTaxScale, NetBalanceParameterRow

_RATE_QUANTUM = Decimal("0.0001")
_ONE = Decimal(1)


@dataclass(frozen=True, slots=True)
class IncomeTaxResult:
    taxable_income: Decimal  # revenu net + loyers
    parts: Decimal
    quotient: Decimal  # taxable_income / parts
    tax: Decimal  # arrondi au centime
    marginal_rate: Decimal  # ratio ; 0 si aucune tranche de seuil < quotient
    average_rate: Decimal  # ratio, 4 décimales ; 0 si revenu imposable nul
    used_rows: tuple[NetBalanceParameterRow, ...]


def compute_fiscal_parts(adults: int, children: int, scale: IncomeTaxScale) -> Decimal:
    """Parts du foyer : adultes + enfants (deux premiers, puis suivants), au moins 1."""
    with localcontext(engine_context()):
        parts = (
            Decimal(adults) * scale.adult_parts
            + Decimal(min(children, 2)) * scale.child_parts_first_two
            + Decimal(max(children - 2, 0)) * scale.child_parts_third_plus
        )
    return max(parts, _ONE)


def compute_income_tax(
    net_income: Decimal,
    property_income: Decimal,
    adults: int,
    children: int,
    scale: IncomeTaxScale,
) -> IncomeTaxResult:
    """Impôt = ``money(parts × barème(revenu imposable / parts))``, un seul arrondi final."""
    with localcontext(engine_context()):
        taxable = net_income + property_income
        parts = compute_fiscal_parts(adults, children, scale)
        quotient = taxable / parts if taxable > ZERO else ZERO

        per_part = ZERO
        marginal = ZERO
        used: list[NetBalanceParameterRow] = list(scale.parts_rows)
        brackets = scale.brackets
        for i, bracket in enumerate(brackets):
            if i == 0 or bracket.threshold < quotient:
                used.extend((bracket.threshold_row, bracket.rate_row))
            if bracket.threshold >= quotient:
                continue
            marginal = bracket.rate
            upper = brackets[i + 1].threshold if i + 1 < len(brackets) else quotient
            width = min(quotient, upper) - bracket.threshold
            if width > ZERO:
                per_part += width * bracket.rate

        tax = money(per_part * parts)
        if taxable > ZERO:
            average = (tax / taxable).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_EVEN)
        else:
            average = ZERO

    return IncomeTaxResult(
        taxable_income=taxable,
        parts=parts,
        quotient=quotient,
        tax=tax,
        marginal_rate=marginal,
        average_rate=average,
        used_rows=tuple(used),
    )
