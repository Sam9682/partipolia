"""French display formatting helpers (presentation layer only)."""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal

_NBSP = "\u00a0"  # non-breaking space (thousands separator and before the euro sign)


def format_number_fr(value: Decimal | int, places: int = 0) -> str:
    """Format a number the French way: ``1 234,50`` (non-breaking thousands space)."""
    quantum = Decimal(1).scaleb(-places)
    quantized = Decimal(value).quantize(quantum, rounding=ROUND_HALF_EVEN)
    sign = "-" if quantized < 0 else ""
    integer_part, _, fraction = f"{abs(quantized):f}".partition(".")
    groups: list[str] = []
    while integer_part:
        groups.insert(0, integer_part[-3:])
        integer_part = integer_part[:-3]
    text = _NBSP.join(groups) or "0"
    if places:
        text = f"{text},{fraction.ljust(places, '0')}"
    return f"{sign}{text}"


def format_eur_fr(value: Decimal | int, places: int = 0) -> str:
    """Format an amount in euros, e.g. ``1 500 €``."""
    return f"{format_number_fr(value, places)}{_NBSP}€"
