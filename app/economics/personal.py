"""Personal savings calculation (module 3 of the specification).

Formulas, per category and per adult:

    avoided expenditure = reference expenditure x reduction fraction
    remaining expenditure = reference expenditure - avoided expenditure
    personal savings = sum of avoided expenditures

Personal savings are a household indicator. They are NOT reduced turnover, NOT
reduced value added and NOT reduced tax revenue; those are computed elsewhere.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money, pct_to_fraction
from app.economics.types import CategorySavings, ModelSnapshot, PersonalSavings

MAX_ADULTS = 10
MONTHS_PER_YEAR = Decimal(12)


def compute_personal_savings(
    snapshot: ModelSnapshot,
    reductions: Mapping[str, Decimal],
    adults: int = 1,
    reference_overrides: Mapping[str, Decimal] | None = None,
) -> PersonalSavings:
    """Compute personal savings for the given per-category reduction percentages.

    Categories missing from ``reductions`` are treated as 0 % reduction. Unknown
    category codes raise ``KeyError``. Reductions must be in ``[0, 100]``.

    ``reference_overrides`` optionally replaces the snapshot's reference annual
    expenditure per category code (per adult). The snapshot is never mutated.
    """
    if not 1 <= adults <= MAX_ADULTS:
        raise ValueError(f"adults must be between 1 and {MAX_ADULTS}")
    overrides: Mapping[str, Decimal] = reference_overrides or {}
    unknown = (set(reductions) | set(overrides)) - set(snapshot.category_codes)
    if unknown:
        raise KeyError(sorted(unknown)[0])

    rows: list[CategorySavings] = []
    with localcontext(engine_context()):
        factor = Decimal(adults)
        for category in snapshot.categories:
            percentage = reductions.get(category.code, ZERO)
            fraction = pct_to_fraction(percentage)
            reference = overrides.get(
                category.code, category.reference_annual_expenditure
            )
            original = reference * factor
            avoided = original * fraction
            rows.append(
                CategorySavings(
                    code=category.code,
                    reduction_percentage=percentage,
                    original_expenditure=money(original),
                    avoided_expenditure=money(avoided),
                    remaining_expenditure=money(original - avoided),
                    annual_savings=money(avoided),
                )
            )
        total_original = sum((r.original_expenditure for r in rows), ZERO)
        total_avoided = sum((r.avoided_expenditure for r in rows), ZERO)
        annual = sum((r.annual_savings for r in rows), ZERO)
        monthly = money(annual / MONTHS_PER_YEAR)
    return PersonalSavings(
        adults=adults,
        categories=tuple(rows),
        original_expenditure=total_original,
        avoided_expenditure=total_avoided,
        remaining_expenditure=money(total_original - total_avoided),
        annual_savings=annual,
        monthly_savings=monthly,
    )
