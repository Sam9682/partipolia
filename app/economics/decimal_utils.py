"""Decimal helpers and the rounding policy of the economic engine.

Rounding policy (applies to the whole engine, documented on the methodology page):

* All intermediate computations use :class:`decimal.Decimal` with an explicit local
  context (28 significant digits). Floating point is never used for money.
* Intermediate values are NOT rounded. Rounding happens only once, at the boundary
  where a value becomes a reported result, using :func:`money` (euro cents,
  banker's rounding ``ROUND_HALF_EVEN``).
* Percentages are exchanged as ``Decimal`` values in ``[0, 100]`` and converted to
  fractions with :func:`pct_to_fraction`.
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from typing import Final

ZERO: Final[Decimal] = Decimal("0")
ONE: Final[Decimal] = Decimal("1")
HUNDRED: Final[Decimal] = Decimal("100")
CENT: Final[Decimal] = Decimal("0.01")

#: Precision used for every engine computation.
PRECISION: Final[int] = 28


def engine_context() -> Context:
    """Return a fresh decimal context used by all engine computations."""
    return Context(prec=PRECISION, rounding=ROUND_HALF_EVEN)


def to_decimal(value: Decimal | int | str) -> Decimal:
    """Convert to ``Decimal`` while rejecting ``float`` (and booleans)."""
    if isinstance(value, bool | float):
        raise TypeError("floating point values are not allowed in financial computations")
    return Decimal(value)


def money(value: Decimal) -> Decimal:
    """Round an amount to euro cents (``ROUND_HALF_EVEN``)."""
    with localcontext(engine_context()):
        return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def pct_to_fraction(percentage: Decimal) -> Decimal:
    """Convert a percentage in ``[0, 100]`` to a fraction in ``[0, 1]``."""
    if not (ZERO <= percentage <= HUNDRED):
        raise ValueError("percentage must be between 0 and 100")
    with localcontext(engine_context()):
        return percentage / HUNDRED
