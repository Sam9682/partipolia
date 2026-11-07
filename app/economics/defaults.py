"""Bootstrap model snapshot, built from the datasets shipped with the package.

The CSV files in ``app/economics/data`` are the single source of the initial
parameters:

* ``insee_tes_a38_2023.csv`` and ``insee_branch_accounts_a38_2023.csv``: official
  INSEE data (input-output table and branch accounts, A38, 2023), produced by
  ``tools/build_insee_datasets.py``;
* ``vat_splits.csv``: legal VAT rates (official) with provisional spending splits;
* ``assumptions_provisional.csv``: every parameter for which no verified official
  figure could be obtained, each labelled ``provisional`` (see ``DATA_GAPS.md``).

The bootstrap snapshot lets the simulator work before any model version has been
published in the database; it is also the content of the seed model version.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from app.economics.categories import DEFAULT_CATEGORIES
from app.economics.csv_import import parse_parameter_csv
from app.economics.parameters import ParameterRow, build_snapshot
from app.economics.types import CategoryParameters, ModelSnapshot

BOOTSTRAP_MODEL_VERSION = "bootstrap-0"
DATA_DIR = Path(__file__).resolve().parent / "data"


def dataset_files() -> list[Path]:
    """Shipped dataset files, in a stable order."""
    return sorted(DATA_DIR.glob("*.csv"))


def load_rows(path: Path) -> list[ParameterRow]:
    result = parse_parameter_csv(path.read_text(encoding="utf-8"))
    if not result.ok:
        raise ValueError(f"{path.name}: {'; '.join(result.errors[:5])}")
    return list(result.rows)


def default_categories() -> list[CategoryParameters]:
    return [
        CategoryParameters(c.code, c.name, c.reference_annual_expenditure)
        for c in DEFAULT_CATEGORIES
    ]


@lru_cache(maxsize=1)
def bootstrap_snapshot() -> ModelSnapshot:
    rows: list[ParameterRow] = []
    labels: list[str] = []
    for path in dataset_files():
        rows.extend(load_rows(path))
        labels.append(f"{path.stem}@bootstrap")
    return build_snapshot(BOOTSTRAP_MODEL_VERSION, labels, default_categories(), rows)
