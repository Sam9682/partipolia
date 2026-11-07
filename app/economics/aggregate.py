"""Aggregate helpers of the economic engine.

* :func:`aggregate_savings` wraps summed avoided spending into the structure the
  engine consumes (the engine is linear in the avoided spending).
* :func:`apportion` splits an amount into euro cents by the largest-remainder
  method so that the parts sum exactly to the (rounded) total.
"""

from __future__ import annotations

from collections.abc import Hashable, Mapping
from decimal import ROUND_FLOOR, Decimal, localcontext
from typing import Final

from app.economics.decimal_utils import CENT, ZERO, engine_context, money
from app.economics.types import CategorySavings, ModelSnapshot, PersonalSavings

MONTHS_PER_YEAR: Final[Decimal] = Decimal(12)

#: Internal tolerance when checking that the parts add up to the total.
SUM_TOLERANCE: Final[Decimal] = Decimal("1e-9")


def aggregate_savings(snapshot: ModelSnapshot, avoided: Mapping[str, Decimal]) -> PersonalSavings:
    """Wrap summed avoided spending into the structure the engine consumes."""
    rows = []
    for category in snapshot.categories:
        value = money(avoided.get(category.code, ZERO))
        rows.append(
            CategorySavings(
                code=category.code,
                reduction_percentage=ZERO,  # not meaningful for an aggregate
                original_expenditure=value,
                avoided_expenditure=value,
                remaining_expenditure=ZERO,
                annual_savings=value,
            )
        )
    total = sum((r.annual_savings for r in rows), ZERO)
    return PersonalSavings(
        adults=1,
        categories=tuple(rows),
        original_expenditure=total,
        avoided_expenditure=total,
        remaining_expenditure=ZERO,
        annual_savings=total,
        monthly_savings=money(total / MONTHS_PER_YEAR),
    )


def apportion[K: Hashable](total: Decimal, parts: Mapping[K, Decimal]) -> dict[K, Decimal]:
    """Split ``total`` into euro cents by largest remainder.

    The sum of the result is exactly ``money(total)``. Raises ``ValueError`` if a
    part is negative or if the parts do not add up to ``total`` (internal
    tolerance ``1e-9``).
    """
    if any(p < ZERO for p in parts.values()):
        raise ValueError("parts must not be negative")
    with localcontext(engine_context()):
        if abs(sum(parts.values(), ZERO) - total) > SUM_TOLERANCE:
            raise ValueError("parts do not add up to the total")
        target = money(total)
        floors = {k: p.quantize(CENT, rounding=ROUND_FLOOR) for k, p in parts.items()}
        missing = int((target - sum(floors.values(), ZERO)) / CENT)
        result = dict(floors)
        # Largest fractional remainder first; insertion order breaks ties (stable sort).
        order = sorted(parts, key=lambda k: parts[k] - floors[k], reverse=True)
        if missing < 0 or missing > len(order):
            raise ValueError("parts do not add up to the total")
        for key in order[:missing]:
            result[key] += CENT
        return result


__all__ = ["SUM_TOLERANCE", "aggregate_savings", "apportion"]
