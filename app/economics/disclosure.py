"""Statistical disclosure control for regional aggregates (pure functions).

Aggregates are stored complete; this module decides at read time what may be
published.

* **Primary suppression**: a cohort smaller than ``min_cohort`` (zero included)
  is withheld.
* **Complementary suppression**: the public residual ``national - sum(published)``
  equals ``sum(suppressed) + unassigned``. When that residual contains exactly one
  non-empty component and it is a withheld cohort, it could be deduced exactly. The
  published cohort with the smallest headcount (then the smallest key) is then also
  withheld. The rule is iterated until a fixed point is reached.
* If the national cohort is itself below the threshold, or if the figures are
  inconsistent (sum of cohorts greater than the national total), everything is
  withheld and the inconsistency is logged.

The same rules apply to region x category cells; withheld cells are merged into an
"other categories" bucket.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, Literal

logger = logging.getLogger(__name__)

#: Same value as ``app.services.aggregation_service.MIN_COHORT_SIZE`` (kept here so
#: that the pure engine does not import the service layer).
MIN_COHORT_SIZE: Final[int] = 20

OTHER_CATEGORIES_KEY: Final[str] = "other"


@dataclass(frozen=True, slots=True)
class Cohort:
    key: str  # region code, or category code
    participants: int
    amount: Decimal  # avoided spending (exact cents)


@dataclass(frozen=True, slots=True)
class RegionRow:
    code: str
    label: str
    participants: int
    amount: Decimal
    categories: tuple[Cohort, ...] = ()


@dataclass(frozen=True, slots=True)
class NationalRow:
    participants: int
    unassigned_participants: int = 0  # citizens without a valid Region


@dataclass(frozen=True, slots=True)
class CategoryCell:
    key: str
    participants: int | None  # None for the "other categories" bucket
    amount: Decimal


@dataclass(frozen=True, slots=True)
class RegionalView:
    code: str
    label: str
    status: Literal["published", "suppressed"]
    participants: int | None = None  # None (never 0) when suppressed
    amount: Decimal | None = None
    categories: tuple[CategoryCell, ...] = ()
    min_cohort: int = MIN_COHORT_SIZE


def primary_suppressed(cohorts: Sequence[Cohort], min_cohort: int = MIN_COHORT_SIZE) -> set[str]:
    """Keys of cohorts strictly smaller than ``min_cohort`` (empty cohorts included)."""
    return {c.key for c in cohorts if c.participants < min_cohort}


def complementary_suppression(
    cohorts: Sequence[Cohort],
    *,
    total_participants: int,
    hidden_residual_count: int,
    min_cohort: int = MIN_COHORT_SIZE,
) -> set[str]:
    """Keys to withhold: primary suppression plus complementary suppression.

    ``hidden_residual_count`` is the headcount of the never-published group
    (citizens without a valid Region); it is part of the public residual.
    """
    all_keys = {c.key for c in cohorts}
    if total_participants < min_cohort:
        return all_keys
    if sum(c.participants for c in cohorts) + hidden_residual_count > total_participants:
        logger.warning(
            "disclosure_inconsistent_totals: cohorts=%d unassigned=%d national=%d; "
            "withholding every cohort",
            sum(c.participants for c in cohorts),
            hidden_residual_count,
            total_participants,
        )
        return all_keys

    suppressed = primary_suppressed(cohorts, min_cohort)
    while True:
        non_empty_hidden = [c for c in cohorts if c.key in suppressed and c.participants > 0]
        components = len(non_empty_hidden) + (1 if hidden_residual_count > 0 else 0)
        if components != 1 or not non_empty_hidden:
            break
        published = [c for c in cohorts if c.key not in suppressed]
        if not published:
            break
        smallest = min(published, key=lambda c: (c.participants, c.key))
        suppressed.add(smallest.key)
    return suppressed


def publish_categories(
    cells: Sequence[Cohort], min_cohort: int = MIN_COHORT_SIZE
) -> tuple[CategoryCell, ...]:
    """Publishable region x category cells; withheld cells merge into "other"."""
    if not cells:
        return ()
    total = sum(c.participants for c in cells)
    withheld = complementary_suppression(
        cells, total_participants=total, hidden_residual_count=0, min_cohort=min_cohort
    )
    shown = sorted((c for c in cells if c.key not in withheld), key=lambda c: c.key)
    out = [CategoryCell(c.key, c.participants, c.amount) for c in shown]
    rest = [c for c in cells if c.key in withheld and c.participants > 0]
    if rest:
        out.append(
            CategoryCell(OTHER_CATEGORIES_KEY, None, sum((c.amount for c in rest), Decimal(0)))
        )
    return tuple(out)


def publish_regions(
    rows: Sequence[RegionRow],
    national: NationalRow | None,
    min_cohort: int = MIN_COHORT_SIZE,
) -> list[RegionalView]:
    """One :class:`RegionalView` per row; withheld Regions carry no figure at all."""
    cohorts = [Cohort(r.code, r.participants, r.amount) for r in rows]
    if national is None:
        withheld = {c.key for c in cohorts}
    else:
        withheld = complementary_suppression(
            cohorts,
            total_participants=national.participants,
            hidden_residual_count=national.unassigned_participants,
            min_cohort=min_cohort,
        )
    views: list[RegionalView] = []
    for row in rows:
        if row.code in withheld:
            views.append(RegionalView(row.code, row.label, "suppressed", min_cohort=min_cohort))
        else:
            views.append(
                RegionalView(
                    row.code,
                    row.label,
                    "published",
                    participants=row.participants,
                    amount=row.amount,
                    categories=publish_categories(row.categories, min_cohort),
                    min_cohort=min_cohort,
                )
            )
    return views


__all__ = [
    "MIN_COHORT_SIZE",
    "OTHER_CATEGORIES_KEY",
    "CategoryCell",
    "Cohort",
    "NationalRow",
    "RegionRow",
    "RegionalView",
    "complementary_suppression",
    "primary_suppressed",
    "publish_categories",
    "publish_regions",
]
