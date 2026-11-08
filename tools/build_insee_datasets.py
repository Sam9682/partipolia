"""Build Partipolia dataset CSVs from official INSEE workbooks (developer tool).

NOT a runtime dependency: it needs ``openpyxl`` (``pip install openpyxl``) and the
downloaded INSEE files. Run from the repository root::

    python tools/build_insee_datasets.py --tes TES_symetrique_A38_2023SD.xlsx \\
        --accounts-dir ./insee --year 2023 --out app/economics/data

Sources (Comptes nationaux annuels, base 2020):

* TES symétrique A38 (2023, semi-définitif):
  https://www.insee.fr/fr/statistiques/fichier/8574722/TES_symetrique_A38_2023SD.xlsx
* 6.201 valeur ajoutée brute par branche (A38):
  https://www.insee.fr/fr/statistiques/fichier/8988821/T_6201_6203_fr.xlsx
* 6.204 rémunération des salariés, 6.205 salaires et traitements bruts,
  6.206 excédent brut d'exploitation par branche (A38):
  https://www.insee.fr/fr/statistiques/fichier/8988821/T_6204_fr.xlsx (6205, 6206 likewise)

Living-standard deciles (D1..D9, euros per year and per consumption unit), INSEE
"Distribution des niveaux de vie", https://www.insee.fr/fr/statistiques/2416808.
``--tes`` and ``--accounts-dir`` are optional; the deciles can be built alone::

    python tools/build_insee_datasets.py --deciles niveaux_de_vie.xlsx \\
        --deciles-year 2024 --out app/economics/data

``--deciles`` also accepts a simple CSV with rows ``decile,valeur`` (D1..D9) when the
INSEE workbook cannot be downloaded. Strict growth D1 < ... < D9 is checked before
writing; the sha256 of the output is printed.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
from decimal import Decimal
from pathlib import Path

import openpyxl

HEADER = [
    "category_code",
    "parameter_type",
    "key",
    "low",
    "central",
    "high",
    "unit",
    "confidence",
    "note",
]
DECILES_HEADER = [
    "decile",
    "niveau_vie_eur_par_uc",
    "annee_reference",
    "source_url",
    "version_table",
]
DECILES_URL = "https://www.insee.fr/fr/statistiques/2416808"
TES_NOTE = "INSEE, TES symétrique A38 base 2020, production domestique au prix de base, {year}"
ACC_NOTE = "INSEE, Comptes nationaux annuels base 2020, tableau {table}, A38, {year}"
# Items published under a coarser code although they contain exactly one A38 branch.
SINGLE_BRANCH_ALIASES = {
    "A5_AZ": "AZ",
    "A17_C1": "CA",  # food, beverages, tobacco
    "A17_C2": "CD",  # coke and refined petroleum
    "A17_C4": "CL",  # transport equipment
    "A5_FZ": "FZ",
    "A17_GZ": "GZ",
    "A17_HZ": "HZ",
    "A17_IZ": "IZ",
    "A10_KZ": "KZ",
    "A10_LZ": "LZ",
}
FIRST_PRODUCT_ROW, LAST_PRODUCT_ROW = 10, 46
OUTPUT_ROW, CI_ACQ_ROW, CODE_ROW = 47, 53, 8
DOMESTIC_RESOURCES_COL = 40  # "Total des ressources domestiques"
RESOURCE_COLS = range(3, 40)  # production by branch
INTERMEDIATE_COLS = range(44, 81)  # intermediate consumption by branch


def _num(value: object) -> Decimal:
    if value is None or value == "":
        return Decimal(0)
    return Decimal(str(value))


def build_tes(path: Path, year: int, out: Path) -> set[str]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb["TES de production domestique"].iter_rows(values_only=True))
    branches = [rows[CODE_ROW][c] for c in RESOURCE_COLS]
    assert branches == [rows[CODE_ROW][c] for c in INTERMEDIATE_COLS], "branch headers differ"
    products = [rows[r][2] for r in range(FIRST_PRODUCT_ROW, LAST_PRODUCT_ROW + 1)]
    assert products == branches, "A38 products and branches must share the same codes"
    note = TES_NOTE.format(year=year)
    lines: list[list[str]] = []
    for col, branch in zip(RESOURCE_COLS, branches, strict=True):
        v = _num(rows[OUTPUT_ROW][col])
        lines.append(["", "io_output", branch, str(v), str(v), str(v), "M EUR", "high", note])
    for col, branch in zip(INTERMEDIATE_COLS, branches, strict=True):
        v = _num(rows[CI_ACQ_ROW][col])
        lines.append(["", "io_ci_total", branch, str(v), str(v), str(v), "M EUR", "high", note])
    # Import share of each product's resources: imports / (domestic resources + imports).
    imports = list(wb["TES des importations"].iter_rows(values_only=True))
    for r, product in zip(range(FIRST_PRODUCT_ROW, LAST_PRODUCT_ROW + 1), products, strict=True):
        assert imports[r][2] == product, "import sheet product order differs"
        domestic, imported = _num(rows[r][DOMESTIC_RESOURCES_COL]), _num(imports[r][5])
        share = (imported / (domestic + imported)).quantize(Decimal("0.000001"))
        lines.append(
            [
                "",
                "io_import_share",
                product,
                str(share),
                str(share),
                str(share),
                "ratio",
                "high",
                note + " ; imports / (domestic resources + imports)",
            ]
        )
    for r, product in zip(range(FIRST_PRODUCT_ROW, LAST_PRODUCT_ROW + 1), products, strict=True):
        for col, branch in zip(INTERMEDIATE_COLS, branches, strict=True):
            v = _num(rows[r][col])
            if v != 0:
                lines.append(
                    [
                        "",
                        "io_flow",
                        f"{product}>{branch}",
                        str(v),
                        str(v),
                        str(v),
                        "M EUR",
                        "high",
                        note,
                    ]
                )
    _write(out, lines)
    return set(branches)


def _account_series(path: Path, sheet: str, year: int, branches: set[str]) -> dict[str, Decimal]:
    """Read one value per A38 branch.

    INSEE lists a branch under its A38 code, except when A5/A17 and A38 coincide
    (e.g. ``A17_CA``, ``A5_AZ``): the same branch is then published only once.
    """
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    rows = list(wb[sheet].iter_rows(values_only=True))
    header = next(r for r in rows if r and str(year) in [str(c) for c in r])
    col = [str(c) for c in header].index(str(year))
    series: dict[str, Decimal] = {}
    for r in rows:
        code = r[0]
        if not isinstance(code, str) or r[col] is None:
            continue
        branch = SINGLE_BRANCH_ALIASES.get(code) or (
            code.removeprefix("A38_") if code.startswith("A38_") else None
        )
        if branch in branches:
            series[branch] = _num(r[col]) * 1000  # Md EUR -> M EUR
    missing = branches - set(series)
    assert not missing, f"{sheet}: missing branches {sorted(missing)}"
    return series


def build_accounts(accounts_dir: Path, year: int, out: Path, branches: set[str]) -> int:
    spec = [
        ("branch_value_added", "T_6201_6203_fr.xlsx", "T_6201 en niveau", "6.201"),
        ("branch_compensation", "T_6204_fr.xlsx", "T_6204 en niveau", "6.204"),
        ("branch_wages", "T_6205_fr.xlsx", "T_6205 en niveau", "6.205"),
        ("branch_ebe", "T_6206_fr.xlsx", "T_6206 en niveau", "6.206"),
    ]
    lines: list[list[str]] = []
    for ptype, filename, sheet, table in spec:
        series = _account_series(accounts_dir / filename, sheet, year, branches)
        for branch, v in sorted(series.items()):
            lines.append(
                [
                    "",
                    ptype,
                    branch,
                    str(v),
                    str(v),
                    str(v),
                    "M EUR",
                    "high",
                    ACC_NOTE.format(table=table, year=year),
                ]
            )
    _write(out, lines)
    return len(lines)


def _write(path: Path, lines: list[list[str]], header: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header or HEADER)
        writer.writerows(lines)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    print(f"{path}: {len(lines)} rows sha256={digest}")


def _decile_key(cell: object) -> int | None:
    """Return 1..9 for a cell such as ``D1``, ``d5`` or ``9``; otherwise None."""
    text = str(cell).strip().upper().removeprefix("D") if cell is not None else ""
    return int(text) if text.isdigit() and 1 <= int(text) <= 9 else None


def _read_decile_values(source: Path) -> dict[int, Decimal]:
    """Read D1..D9 from a simple CSV (decile,valeur) or from an INSEE workbook.

    In a workbook, the first row whose first cell is D1..D9 and whose next numeric
    cell is a value is used for each decile.
    """
    pairs: list[tuple[object, ...]]
    if source.suffix.lower() in {".xlsx", ".xlsm"}:
        wb = openpyxl.load_workbook(source, read_only=True, data_only=True)
        pairs = [row for ws in wb.worksheets for row in ws.iter_rows(values_only=True)]
    else:
        with source.open(newline="", encoding="utf-8-sig") as handle:
            pairs = [tuple(row) for row in csv.reader(handle)]
    values: dict[int, Decimal] = {}
    for row in pairs:
        if not row:
            continue
        key = _decile_key(row[0])
        if key is None or key in values:
            continue
        for cell in row[1:]:
            try:
                value = Decimal(str(cell).strip().replace(" ", "").replace(",", "."))
            except ArithmeticError:
                continue
            if value.is_finite():
                values[key] = value
                break
    missing = sorted(set(range(1, 10)) - set(values))
    if missing:
        raise SystemExit(f"{source}: déciles manquants : {['D' + str(k) for k in missing]}")
    return values


def build_deciles(source: Path, year: int, out: Path) -> None:
    """Write the INSEE living-standard deciles CSV (D1..D9) to ``out`` (a file path)."""
    values = _read_decile_values(source)
    for k in range(2, 10):
        if values[k] <= values[k - 1]:
            raise SystemExit(f"D{k} doit être strictement supérieur à D{k - 1}")
    if values[1] <= 0:
        raise SystemExit("D1 doit être strictement positif")
    lines = [
        [
            f"D{k}",
            format(values[k].normalize(), "f"),
            str(year),
            DECILES_URL,
            f"insee-niveaux-de-vie-{year}",
        ]
        for k in range(1, 10)
    ]
    _write(out, lines, DECILES_HEADER)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tes", type=Path, default=None)
    parser.add_argument("--accounts-dir", type=Path, default=None)
    parser.add_argument("--year", type=int, default=2023)
    parser.add_argument(
        "--deciles", type=Path, default=None, help="INSEE xlsx or CSV decile,valeur"
    )
    parser.add_argument("--deciles-year", type=int, default=2024)
    parser.add_argument("--out", type=Path, default=Path("app/economics/data"))
    args = parser.parse_args()
    if args.deciles is None and (args.tes is None or args.accounts_dir is None):
        parser.error("--tes et --accounts-dir sont requis sauf avec --deciles seul")
    if args.deciles is not None:
        build_deciles(
            args.deciles,
            args.deciles_year,
            args.out / f"insee_niveau_vie_deciles_{args.deciles_year}.csv",
        )
    if args.tes is not None and args.accounts_dir is not None:
        branches = build_tes(args.tes, args.year, args.out / f"insee_tes_a38_{args.year}.csv")
        build_accounts(
            args.accounts_dir,
            args.year,
            args.out / f"insee_branch_accounts_a38_{args.year}.csv",
            branches,
        )


if __name__ == "__main__":
    main()
