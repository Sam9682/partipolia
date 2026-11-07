"""Pure computation of the gap between a scenario rate and the declared mobilisation rate.

Evaluation order (no division is ever performed by 0):

1. population absent or <= 0 -> ``unavailable``
2. participants < ``min_cohort`` -> ``suppressed`` (no rate, gap or headcount exposed)
3. declared rate > 100 % -> ``inconsistent`` (no rate exposed)
4. otherwise -> ``ok`` with ``declared_rate`` and ``gap_pp`` rounded to 2 decimals.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Final, Literal

from app.economics.decimal_utils import HUNDRED, ZERO, engine_context, money

MobilisationStatus = Literal["ok", "unavailable", "suppressed", "inconsistent"]

#: Default minimal cohort size below which no figure is exposed.
DEFAULT_MIN_COHORT: Final[int] = 20


@dataclass(frozen=True, slots=True)
class MobilisationGapResult:
    """Result of :func:`mobilisation_gap`.

    ``declared_rate`` and ``gap_pp`` are only set when ``status == "ok"``;
    ``participants`` is only exposed in that case too.
    """

    status: MobilisationStatus
    declared_rate: Decimal | None = None
    gap_pp: Decimal | None = None
    participants: int | None = None


def mobilisation_gap(
    participants: int,
    population: int | None,
    scenario_rate: Decimal,
    min_cohort: int = DEFAULT_MIN_COHORT,
) -> MobilisationGapResult:
    """Compare ``scenario_rate`` (in %) to ``participants / population × 100``."""
    if population is None or population <= 0:
        return MobilisationGapResult(status="unavailable")
    if participants < min_cohort:
        return MobilisationGapResult(status="suppressed")
    with localcontext(engine_context()):
        declared = Decimal(participants) * HUNDRED / Decimal(population)
        if declared > HUNDRED:
            return MobilisationGapResult(status="inconsistent")
        declared = max(declared, ZERO)
        gap = scenario_rate - declared
    return MobilisationGapResult(
        status="ok",
        declared_rate=money(declared),
        gap_pp=money(gap),
        participants=participants,
    )
