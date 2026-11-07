"""CSV parsing of fiscal parameter datasets (pure, no database).

Expected header::

    category_code,parameter_type,key,low,central,high,unit,confidence,note

``category_code`` may be empty for national (non category-scoped) parameters.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from app.economics.parameters import ParameterRow, parse_decimal, validate_rows
from app.economics.types import Confidence

REQUIRED_COLUMNS: tuple[str, ...] = (
    "category_code",
    "parameter_type",
    "key",
    "low",
    "central",
    "high",
    "unit",
    "confidence",
)
MAX_ROWS = 50_000


@dataclass(frozen=True, slots=True)
class CsvParseResult:
    rows: tuple[ParameterRow, ...]
    errors: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_parameter_csv(text: str) -> CsvParseResult:
    """Parse and validate a parameter CSV; never raises on malformed content."""
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    header = tuple(reader.fieldnames or ())
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        return CsvParseResult((), (f"missing columns: {', '.join(missing)}",))

    rows: list[ParameterRow] = []
    errors: list[str] = []
    for line, raw in enumerate(reader, start=2):
        if len(rows) >= MAX_ROWS:
            errors.append(f"too many rows (max {MAX_ROWS})")
            break
        try:
            confidence = Confidence((raw.get("confidence") or "").strip().lower())
            rows.append(
                ParameterRow(
                    category_code=(raw.get("category_code") or "").strip() or None,
                    parameter_type=(raw.get("parameter_type") or "").strip(),
                    key=(raw.get("key") or "").strip(),
                    low=parse_decimal(raw.get("low") or ""),
                    central=parse_decimal(raw.get("central") or ""),
                    high=parse_decimal(raw.get("high") or ""),
                    unit=(raw.get("unit") or "").strip(),
                    confidence=confidence,
                    note=(raw.get("note") or "").strip(),
                )
            )
        except ValueError as exc:
            errors.append(f"line {line}: {exc}")
    if not errors:
        errors.extend(validate_rows(rows))
    return CsvParseResult(tuple(rows), tuple(errors))
