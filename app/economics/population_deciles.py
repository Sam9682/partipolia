"""Chargement et validation de la table des déciles de niveau de vie (INSEE).

Aucun accès réseau : seul le CSV statique du dépôt est lu.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from functools import cache
from pathlib import Path

DEFAULT_PATH = Path(__file__).parent / "data" / "insee_niveau_vie_deciles_2024.csv"

EXPECTED_HEADER = [
    "decile",
    "niveau_vie_eur_par_uc",
    "annee_reference",
    "source_url",
    "version_table",
]
DECILE_COUNT = 9


class DecileTableError(ValueError):
    """Message nommant le fichier et la ligne, ou le décile fautif."""


@dataclass(frozen=True, slots=True)
class DecileRow:
    decile: int  # 1..9
    niveau_vie: Decimal  # EUR par an et par UC
    reference_year: int
    source_url: str
    table_version: str


@dataclass(frozen=True, slots=True)
class DecileTable:
    rows: tuple[DecileRow, ...]  # D1..D9, strictement croissants

    @property
    def version(self) -> str:
        return self.rows[0].table_version

    @property
    def source_url(self) -> str:
        return self.rows[0].source_url

    @property
    def reference_year(self) -> int:
        return self.rows[0].reference_year


def _parse_row(record: list[str], filename: str, line: int, index: int) -> DecileRow:
    where = f"{filename}:{line}"
    if len(record) != len(EXPECTED_HEADER):
        raise DecileTableError(
            f"{where}: {len(EXPECTED_HEADER)} colonnes attendues, {len(record)} trouvées"
        )
    label, raw_value, raw_year, url, version = (c.strip() for c in record)
    expected_label = f"D{index}"
    if label != expected_label:
        raise DecileTableError(f"{where}: décile {expected_label} attendu, '{label}' trouvé")
    try:
        value = Decimal(raw_value)
    except InvalidOperation:
        raise DecileTableError(f"{where}: niveau de vie invalide '{raw_value}'") from None
    if not value.is_finite() or value <= 0:
        raise DecileTableError(f"{where}: niveau de vie doit être fini et strictement positif")
    try:
        year = int(raw_year)
    except ValueError:
        raise DecileTableError(f"{where}: année de référence invalide '{raw_year}'") from None
    if year <= 0:
        raise DecileTableError(f"{where}: année de référence invalide '{raw_year}'")
    if not url:
        raise DecileTableError(f"{where}: source_url vide")
    if not version:
        raise DecileTableError(f"{where}: version_table vide")
    return DecileRow(index, value, year, url, version)


def parse_decile_table(text: str, filename: str) -> DecileTable:
    """Valide et construit la table à partir du contenu CSV."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise DecileTableError(f"{filename}:1: fichier vide") from None
    if [h.strip() for h in header] != EXPECTED_HEADER:
        raise DecileTableError(f"{filename}:1: en-tête attendu {','.join(EXPECTED_HEADER)}")

    rows: list[DecileRow] = []
    for record in reader:
        if not record or all(not c.strip() for c in record):
            continue
        line = reader.line_num
        index = len(rows) + 1
        if index > DECILE_COUNT:
            raise DecileTableError(
                f"{filename}:{line}: ligne en trop, {DECILE_COUNT} déciles attendus"
            )
        rows.append(_parse_row(record, filename, line, index))
    if len(rows) != DECILE_COUNT:
        raise DecileTableError(
            f"{filename}: {DECILE_COUNT} lignes D1..D{DECILE_COUNT} attendues, {len(rows)} trouvées"
        )

    first = rows[0]
    for row in rows[1:]:
        if (row.reference_year, row.source_url, row.table_version) != (
            first.reference_year,
            first.source_url,
            first.table_version,
        ):
            raise DecileTableError(
                f"{filename}: D{row.decile} a une année, URL ou version différente de D1"
            )
    for prev, row in zip(rows, rows[1:], strict=False):
        if row.niveau_vie <= prev.niveau_vie:
            raise DecileTableError(
                f"D{row.decile} doit être strictement supérieur à D{prev.decile}"
            )
    return DecileTable(rows=tuple(rows))


@cache
def load_decile_table(path: Path = DEFAULT_PATH) -> DecileTable:
    """Charge la table depuis le fichier (mise en cache, sans réseau)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DecileTableError(f"{path}:1: fichier illisible ({exc})") from exc
    return parse_decile_table(text, str(path))
