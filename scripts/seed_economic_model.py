"""Seed the economic model: import the shipped datasets and publish model ``v1``.

Idempotent: if a model version with the label already exists, nothing is changed.

    python -m scripts.seed_economic_model [--label v1]

Datasets come from ``app/economics/data/*.csv`` (INSEE, legal VAT rates and the
explicitly provisional assumptions listed in ``DATA_GAPS.md``).
"""

from __future__ import annotations

import argparse
import asyncio
import re
from datetime import date

from app.core.database import SessionLocal
from app.economics.defaults import dataset_files
from app.models.economic_dataset import ModelVersion
from app.services.economic_data_service import DatasetMetadata, EconomicDataService
from sqlalchemy import select

# Metadata of each shipped dataset (source, URL, publication date, reference year).
CATALOG: dict[str, DatasetMetadata] = {
    "insee_tes_a38_2023": DatasetMetadata(
        name="insee_tes_a38_2023",
        source="INSEE - Comptes nationaux (base 2020) - TES symetrique A38",
        source_url="https://www.insee.fr/fr/statistiques/8574722",
        reference_year=2023,
        publication_date=date(2025, 10, 1),
        methodology=(
            "Domestic production at basic prices, intermediate consumption matrix and import shares "
            "by product, extracted by tools/build_insee_datasets.py."
        ),
    ),
    "insee_branch_accounts_a38_2023": DatasetMetadata(
        name="insee_branch_accounts_a38_2023",
        source="INSEE - Comptes nationaux annuels (base 2020) - tableaux 6.201, 6.204, 6.205, 6.206",
        source_url="https://www.insee.fr/fr/statistiques/8988821",
        reference_year=2023,
        publication_date=date(2026, 5, 28),
        methodology="Value added, compensation, wages and gross operating surplus by A38 branch.",
    ),
    "vat_splits": DatasetMetadata(
        name="vat_splits",
        source="service-public.gouv.fr / economie.gouv.fr (legal VAT rates); splits are assumptions",
        source_url="https://entreprendre.service-public.gouv.fr/vosdroits/F37605",
        reference_year=2026,
        methodology="Legal rates are official; the share of spending under each rate is provisional.",
    ),
    "assumptions_provisional": DatasetMetadata(
        name="assumptions_provisional",
        source="Partipolia - provisional assumptions awaiting official data (see DATA_GAPS.md)",
        source_url="",
        reference_year=2026,
        methodology="Every parameter is labelled provisional or medium confidence with a note.",
    ),
}


async def seed(label: str) -> bool:
    async with SessionLocal() as session:
        existing = await session.execute(select(ModelVersion).where(ModelVersion.label == label))
        if existing.scalar_one_or_none() is not None:
            return False
        service = EconomicDataService(session)
        ids: list[int] = []
        for path in dataset_files():
            meta = CATALOG.get(
                path.stem,
                DatasetMetadata(
                    name=path.stem, source="Partipolia", source_url="", reference_year=2026
                ),
            )
            dataset = await service.import_csv(meta, path.read_text(encoding="utf-8"), None)
            ids.append(dataset.id)
        version = await service.create_model_version(label, ids, "Initial model", None)
        await service.publish(version.id, None)
        await session.commit()
        return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", default="v1")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9._-]+", args.label):
        raise SystemExit("invalid label")
    created = asyncio.run(seed(args.label))
    print(f"Model {args.label} published." if created else f"Model {args.label} already exists.")


if __name__ == "__main__":
    main()
