"""Economic data management: dataset import, model versions, publication.

Rules:

* A dataset is imported from a CSV, validated, and stored as flat parameter rows.
* A model version groups validated datasets. Publishing checks completeness,
  builds a snapshot to prove it is usable, and freezes the version.
* Published model versions (and the datasets attached to them) are immutable, so
  every historical simulation stays reproducible.
* Every administrative change is written to the audit log.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.economics.csv_import import parse_parameter_csv
from app.economics.parameters import ParameterRow, build_snapshot, missing_parameters
from app.economics.types import CategoryParameters, Confidence, ModelSnapshot
from app.models.consumption_category import ConsumptionCategory
from app.models.economic_dataset import EconomicDataset, FiscalParameter, ModelVersion
from app.services.audit_service import AuditService


class EconomicDataError(Exception):
    """Base class of expected, user-facing data management errors."""


class DatasetValidationError(EconomicDataError):
    def __init__(self, errors: Sequence[str]) -> None:
        super().__init__("; ".join(errors[:5]))
        self.errors = list(errors)


class NotFoundError(EconomicDataError):
    pass


class ImmutableVersionError(EconomicDataError):
    pass


class PublishError(EconomicDataError):
    pass


@dataclass(frozen=True, slots=True)
class DatasetMetadata:
    name: str
    source: str
    source_url: str
    reference_year: int
    publication_date: date | None = None
    methodology: str = ""


@dataclass(frozen=True, slots=True)
class VersionDiff:
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def _identity(row: ParameterRow) -> str:
    return f"{row.category_code or '-'}/{row.parameter_type}/{row.key}"


class EconomicDataService:
    def __init__(self, session: AsyncSession, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    # ------------------------------------------------------------------ #
    # Datasets
    # ------------------------------------------------------------------ #
    async def import_csv(
        self, meta: DatasetMetadata, csv_text: str, actor_id: int | None
    ) -> EconomicDataset:
        """Validate and store a CSV dataset as the next version of ``meta.name``."""
        result = parse_parameter_csv(csv_text)
        if not result.ok:
            raise DatasetValidationError(result.errors)

        categories = {c.code: c.id for c in await self._categories()}
        unknown = {r.category_code for r in result.rows if r.category_code} - set(categories)
        if unknown:
            raise DatasetValidationError([f"unknown category: {sorted(unknown)[0]}"])

        current = (
            await self._session.execute(
                select(func.max(EconomicDataset.version)).where(EconomicDataset.name == meta.name)
            )
        ).scalar()
        dataset = EconomicDataset(
            name=meta.name,
            source=meta.source,
            source_url=meta.source_url,
            publication_date=meta.publication_date,
            reference_year=meta.reference_year,
            methodology=meta.methodology,
            checksum=hashlib.sha256(csv_text.encode("utf-8")).hexdigest(),
            imported_by_id=actor_id,
            validation_status="validated",
            validation_errors="",
            version=(current or 0) + 1,
        )
        dataset.parameters = [
            FiscalParameter(
                category_id=categories[r.category_code] if r.category_code else None,
                parameter_type=r.parameter_type,
                parameter_key=r.key,
                value_low=r.low,
                parameter_value=r.central,
                value_high=r.high,
                unit=r.unit,
                confidence_level=r.confidence.value,
                note=r.note,
            )
            for r in result.rows
        ]
        self._session.add(dataset)
        await self._session.flush()
        await self._audit.record(
            action="economic_dataset.import",
            entity_type="economic_dataset",
            entity_id=dataset.id,
            new_data={
                "name": dataset.name,
                "version": dataset.version,
                "checksum": dataset.checksum,
                "rows": len(result.rows),
                "actor_id": actor_id,
            },
        )
        return dataset

    async def list_datasets(self) -> list[EconomicDataset]:
        rows = await self._session.execute(
            select(EconomicDataset).order_by(EconomicDataset.name, EconomicDataset.version)
        )
        return list(rows.scalars())

    # ------------------------------------------------------------------ #
    # Model versions
    # ------------------------------------------------------------------ #
    async def create_model_version(
        self, label: str, dataset_ids: Sequence[int], notes: str, actor_id: int | None
    ) -> ModelVersion:
        existing = await self._session.execute(
            select(ModelVersion).where(ModelVersion.label == label)
        )
        if existing.scalar_one_or_none() is not None:
            raise EconomicDataError("model version label already exists")
        datasets = await self._datasets(dataset_ids)
        invalid = [d for d in datasets if d.validation_status != "validated"]
        if invalid:
            raise DatasetValidationError(
                [f"dataset {d.name} v{d.version} is not validated" for d in invalid]
            )
        version = ModelVersion(label=label, notes=notes, status="draft")
        version.datasets = list(datasets)
        self._session.add(version)
        await self._session.flush()
        await self._audit.record(
            action="model_version.create",
            entity_type="model_version",
            entity_id=version.id,
            new_data={"label": label, "datasets": list(dataset_ids), "actor_id": actor_id},
        )
        return version

    async def publish(self, model_version_id: int, actor_id: int | None) -> ModelVersion:
        version = await self._get_version(model_version_id)
        if version.status == "published":
            raise ImmutableVersionError("model version is already published")
        categories = await self._category_params()
        rows = await self._rows_for(version)
        problems = missing_parameters(categories, rows)
        if problems:
            raise PublishError("missing parameters: " + ", ".join(problems))
        try:
            build_snapshot(version.label, self._dataset_labels(version), categories, rows)
        except ValueError as exc:
            raise PublishError(str(exc)) from exc
        version.status = "published"
        version.published_at = datetime.now(UTC)
        version.published_by_id = actor_id
        await self._session.flush()
        await self._audit.record(
            action="model_version.publish",
            entity_type="model_version",
            entity_id=version.id,
            new_data={"label": version.label, "actor_id": actor_id},
        )
        return version

    async def list_versions(self, published_only: bool = False) -> list[ModelVersion]:
        query = select(ModelVersion).order_by(ModelVersion.id)
        if published_only:
            query = query.where(ModelVersion.status == "published")
        return list((await self._session.execute(query)).scalars())

    async def get_snapshot(self, model_version_id: int) -> ModelSnapshot:
        """Rebuild the snapshot of a version (used for reproducibility)."""
        version = await self._get_version(model_version_id)
        return await self._snapshot_of(version)

    async def get_latest_published(self) -> tuple[ModelVersion, ModelSnapshot] | None:
        row = await self._session.execute(
            select(ModelVersion)
            .where(ModelVersion.status == "published")
            .order_by(ModelVersion.published_at.desc(), ModelVersion.id.desc())
            .limit(1)
        )
        version = row.scalar_one_or_none()
        if version is None:
            return None
        return version, await self._snapshot_of(version)

    async def compare(self, from_id: int, to_id: int) -> VersionDiff:
        before = {_identity(r): r for r in await self._rows_for(await self._get_version(from_id))}
        after = {_identity(r): r for r in await self._rows_for(await self._get_version(to_id))}
        return VersionDiff(
            added=tuple(sorted(set(after) - set(before))),
            removed=tuple(sorted(set(before) - set(after))),
            changed=tuple(sorted(k for k in set(before) & set(after) if before[k] != after[k])),
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    async def _snapshot_of(self, version: ModelVersion) -> ModelSnapshot:
        return build_snapshot(
            version.label,
            self._dataset_labels(version),
            await self._category_params(),
            await self._rows_for(version),
        )

    @staticmethod
    def _dataset_labels(version: ModelVersion) -> list[str]:
        return sorted(f"{d.name}@v{d.version}" for d in version.datasets)

    async def _get_version(self, model_version_id: int) -> ModelVersion:
        version = await self._session.get(ModelVersion, model_version_id)
        if version is None:
            raise NotFoundError("model version not found")
        return version

    async def _datasets(self, ids: Sequence[int]) -> list[EconomicDataset]:
        if not ids:
            raise EconomicDataError("at least one dataset is required")
        rows = await self._session.execute(
            select(EconomicDataset).where(EconomicDataset.id.in_(ids))
        )
        found = list(rows.scalars())
        if len(found) != len(set(ids)):
            raise NotFoundError("dataset not found")
        return found

    async def _categories(self) -> list[ConsumptionCategory]:
        rows = await self._session.execute(
            select(ConsumptionCategory)
            .where(ConsumptionCategory.active.is_(True))
            .order_by(ConsumptionCategory.id)
        )
        return list(rows.scalars())

    async def _category_params(self) -> list[CategoryParameters]:
        return [
            CategoryParameters(c.code, c.name, c.reference_annual_expenditure)
            for c in await self._categories()
        ]

    async def _rows_for(self, version: ModelVersion) -> list[ParameterRow]:
        ids = [d.id for d in version.datasets]
        if not ids:
            return []
        query = (
            select(FiscalParameter, ConsumptionCategory.code)
            .outerjoin(ConsumptionCategory, FiscalParameter.category_id == ConsumptionCategory.id)
            .where(FiscalParameter.dataset_id.in_(ids))
            .order_by(FiscalParameter.id)
        )
        result = await self._session.execute(query)
        return [
            ParameterRow(
                category_code=code,
                parameter_type=p.parameter_type,
                key=p.parameter_key,
                low=p.value_low,
                central=p.parameter_value,
                high=p.value_high,
                unit=p.unit,
                confidence=Confidence(p.confidence_level),
                note=p.note,
            )
            for p, code in result.all()
        ]
