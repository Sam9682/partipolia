"""Direct fiscal impact: VAT embedded in the avoided final expenditure.

For a TTC (tax-inclusive) amount and a VAT rate ``r``::

    VAT = TTC x r / (1 + r)          turnover (HT) = TTC - VAT

Several VAT rates can apply within one category (see :class:`VatProfile`). This
module only covers VAT on final consumption; avoided fuel excise is handled by
the fuel module, and supply-chain / labour taxes by the indirect and induced
modules, so that no tax flow is counted twice.

Indicators are kept apart: ``avoided_ttc`` (what the household does not spend),
``turnover_ht`` (reduced commercial turnover) and ``vat`` (reduced VAT receipts).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ONE, ZERO, engine_context, money
from app.economics.types import ModelSnapshot, PersonalSavings, VatProfile


@dataclass(frozen=True, slots=True)
class VatLine:
    rate: Decimal
    ttc: Decimal
    vat: Decimal


@dataclass(frozen=True, slots=True)
class CategoryDirectImpact:
    code: str
    avoided_ttc: Decimal
    turnover_ht: Decimal
    vat: Decimal
    lines: tuple[VatLine, ...]


@dataclass(frozen=True, slots=True)
class DirectImpact:
    scenario: str
    categories: tuple[CategoryDirectImpact, ...]
    avoided_ttc: Decimal
    turnover_ht: Decimal
    vat: Decimal


def vat_from_ttc(ttc: Decimal, rate: Decimal) -> Decimal:
    """VAT contained in a TTC amount (unrounded)."""
    with localcontext(engine_context()):
        return ttc * rate / (ONE + rate)


def category_direct_impact(
    code: str, avoided_ttc: Decimal, profile: VatProfile, scenario: str
) -> CategoryDirectImpact:
    """Direct VAT for one category; rounded parts always add up to the totals."""
    components = profile.splits[scenario]
    with localcontext(engine_context()):
        raw = [(c.rate, avoided_ttc * c.share) for c in components]
        vat_total = money(sum((vat_from_ttc(ttc, rate) for rate, ttc in raw), ZERO))
        lines: list[VatLine] = []
        ttc_allocated = ZERO
        vat_allocated = ZERO
        for index, (rate, ttc_raw) in enumerate(raw):
            last = index == len(raw) - 1
            # The last line absorbs the rounding remainder so the lines sum exactly.
            ttc_line = avoided_ttc - ttc_allocated if last else money(ttc_raw)
            vat_line = vat_total - vat_allocated if last else money(vat_from_ttc(ttc_raw, rate))
            ttc_allocated += ttc_line
            vat_allocated += vat_line
            lines.append(VatLine(rate=rate, ttc=ttc_line, vat=vat_line))
        return CategoryDirectImpact(
            code=code,
            avoided_ttc=avoided_ttc,
            turnover_ht=avoided_ttc - vat_total,
            vat=vat_total,
            lines=tuple(lines),
        )


def compute_direct_impact(
    snapshot: ModelSnapshot, savings: PersonalSavings, scenario: str = "central"
) -> DirectImpact:
    """Direct VAT impact of the avoided expenditure of ``savings``."""
    rows: list[CategoryDirectImpact] = []
    for category in savings.categories:
        profile = snapshot.vat_profiles.get(category.code)
        if profile is None:
            raise KeyError(f"no VAT profile for category {category.code!r}")
        rows.append(
            category_direct_impact(category.code, category.avoided_expenditure, profile, scenario)
        )
    return DirectImpact(
        scenario=scenario,
        categories=tuple(rows),
        avoided_ttc=sum((r.avoided_ttc for r in rows), ZERO),
        turnover_ht=sum((r.turnover_ht for r in rows), ZERO),
        vat=sum((r.vat for r in rows), ZERO),
    )
