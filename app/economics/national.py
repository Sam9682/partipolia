"""National simulation inputs and avoided spending (pure, no database).

Formula, per category (unrounded until the very end):

    avoided = population x participation/100 x reference expenditure x reduction/100

The result is rounded to euro cents ONCE, with :func:`money`, on that product.
Categories without a reduction > 0 are absent from the result.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, localcontext
from typing import Literal

from app.core.logging import get_logger
from app.economics.decimal_utils import ZERO, engine_context, money, pct_to_fraction
from app.economics.types import Confidence, ModelSnapshot

logger = get_logger(__name__)

ConfidenceLabel = Literal["official", "provisional", "low"]
Horizon = Literal["short", "medium", "long"]

POPULATION_PARAMETER = "reference_population"
POPULATION_KEY = "adults"

#: Display vocabulary of reference datasets (decision D10).
_CONFIDENCE_LABELS: dict[Confidence, ConfidenceLabel] = {
    Confidence.HIGH: "official",
    Confidence.MEDIUM: "provisional",
    Confidence.PROVISIONAL: "provisional",
    Confidence.LOW: "low",
}

_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


class MissingPopulationError(Exception):
    """The model snapshot has no usable ``reference_population`` parameter."""


@dataclass(frozen=True, slots=True)
class NationalScenario:
    """Parameters of a national scenario (percentages in [0, 100])."""

    participation_rate: Decimal
    reductions: Mapping[str, Decimal]
    horizon: Horizon = "medium"


@dataclass(frozen=True, slots=True)
class ReferencePopulation:
    """Adult reference population with its provenance."""

    value: Decimal
    source: str
    as_of: date | None
    confidence: Confidence
    confidence_label: ConfidenceLabel


def confidence_label(confidence: Confidence) -> ConfidenceLabel:
    """Map an engine confidence to the displayed vocabulary (D10)."""
    return _CONFIDENCE_LABELS[confidence]


def reference_population(snapshot: ModelSnapshot) -> ReferencePopulation:
    """Read the ``reference_population`` parameter (key ``adults``).

    Raises :class:`MissingPopulationError` if absent or not strictly positive.
    """
    try:
        parameter = snapshot.param(POPULATION_PARAMETER, POPULATION_KEY)
    except KeyError as exc:
        raise MissingPopulationError("reference_population is not defined") from exc
    if parameter.central <= ZERO:
        raise MissingPopulationError("reference_population must be positive")
    match = _ISO_DATE.search(parameter.note)
    as_of: date | None = None
    if match:
        try:
            as_of = date.fromisoformat(match.group(1))
        except ValueError:
            as_of = None
    return ReferencePopulation(
        value=parameter.central,
        source=parameter.note,
        as_of=as_of,
        confidence=parameter.confidence,
        confidence_label=confidence_label(parameter.confidence),
    )


def with_population_fallback(snapshot: ModelSnapshot, fallback: ModelSnapshot) -> ModelSnapshot:
    """Complete a snapshot lacking a usable ``reference_population``.

    The snapshot is returned unchanged when it already has a valid population.
    Otherwise a copy is returned with the population of ``fallback`` (forced to
    PROVISIONAL confidence so that a warning is always shown). The original
    snapshot is never mutated. ``MissingPopulationError`` is raised if the
    fallback itself has no usable population.
    """
    try:
        reference_population(snapshot)
    except MissingPopulationError:
        pass
    else:
        return snapshot
    reference_population(fallback)  # raises if the fallback is unusable too
    key = (POPULATION_PARAMETER, None, POPULATION_KEY)
    provisional = replace(fallback.parameters[key], confidence=Confidence.PROVISIONAL)
    logger.warning("national_population_fallback", model_version=snapshot.model_version)
    return replace(snapshot, parameters={**snapshot.parameters, key: provisional})


def avoided_by_category(snapshot: ModelSnapshot, scenario: NationalScenario) -> dict[str, Decimal]:
    """Annual avoided spending (EUR, TTC) per category, rounded once to the cent.

    Unknown category codes raise ``KeyError``; categories whose reduction is not
    strictly positive are omitted.
    """
    unknown = set(scenario.reductions) - set(snapshot.category_codes)
    if unknown:
        raise KeyError(sorted(unknown)[0])
    population = reference_population(snapshot).value
    result: dict[str, Decimal] = {}
    with localcontext(engine_context()):
        participants = population * pct_to_fraction(scenario.participation_rate)
        for category in snapshot.categories:
            reduction = scenario.reductions.get(category.code, ZERO)
            if reduction <= ZERO:
                continue
            raw = participants * category.reference_annual_expenditure * pct_to_fraction(reduction)
            result[category.code] = money(raw)
    return result


__all__ = [
    "ConfidenceLabel",
    "MissingPopulationError",
    "NationalScenario",
    "ReferencePopulation",
    "avoided_by_category",
    "confidence_label",
    "reference_population",
    "with_population_fallback",
]
