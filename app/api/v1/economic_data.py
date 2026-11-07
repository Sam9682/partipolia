"""Economic data endpoints.

Public: ``GET /economic-data/sources`` and ``/economic-data/versions`` (only
published model versions and the datasets they use).

Administration (``data_admin`` or ``super_admin``): import, version creation,
publication and comparison. Nothing becomes visible to the simulator until a
model version is explicitly published.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.economic_data import (
    DatasetImportRequest,
    DatasetOut,
    ModelVersionCreateRequest,
    ModelVersionOut,
    PublishRequest,
    VersionDiffOut,
)
from app.services.economic_data_service import (
    DatasetMetadata,
    DatasetValidationError,
    EconomicDataError,
    EconomicDataService,
    ImmutableVersionError,
    NotFoundError,
    PublishError,
)

router = APIRouter()
admin_router = APIRouter()

DataAdmin = Annotated[UserPublic, Depends(require_roles(Role.DATA_ADMIN))]


def get_service(
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> EconomicDataService:
    return EconomicDataService(session)


Service = Annotated[EconomicDataService, Depends(get_service)]


def _http(exc: EconomicDataError) -> HTTPException:
    if isinstance(exc, NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ImmutableVersionError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, DatasetValidationError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, "; ".join(exc.errors[:20]))
    if isinstance(exc, PublishError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


# --------------------------------------------------------------------- public
@router.get("/sources", response_model=list[DatasetOut], summary="Sources de données utilisées")
async def list_sources(service: Service) -> list[DatasetOut]:
    seen: dict[int, DatasetOut] = {}
    for version in await service.list_versions(published_only=True):
        for dataset in version.datasets:
            seen[dataset.id] = DatasetOut.model_validate(dataset)
    return sorted(seen.values(), key=lambda d: (d.name, d.version))


@router.get(
    "/versions", response_model=list[ModelVersionOut], summary="Versions publiées du modèle"
)
async def list_versions(service: Service) -> list[ModelVersionOut]:
    return [
        ModelVersionOut(
            id=v.id,
            label=v.label,
            status=v.status,
            notes=v.notes,
            created_at=v.created_at,
            published_at=v.published_at,
            datasets=[DatasetOut.model_validate(d) for d in v.datasets],
        )
        for v in await service.list_versions(published_only=True)
    ]


# ---------------------------------------------------------------------- admin
@admin_router.post(
    "/economic-data/import",
    response_model=DatasetOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(default_rate_limit("admin-import"))],
    summary="Importer un jeu de paramètres (CSV)",
)
async def import_dataset(
    payload: DatasetImportRequest, service: Service, admin: DataAdmin
) -> DatasetOut:
    meta = DatasetMetadata(
        name=payload.name,
        source=payload.source,
        source_url=payload.source_url,
        reference_year=payload.reference_year,
        publication_date=payload.publication_date,
        methodology=payload.methodology,
    )
    try:
        dataset = await service.import_csv(meta, payload.csv_text, admin.id)
    except EconomicDataError as exc:
        raise _http(exc) from exc
    return DatasetOut.model_validate(dataset)


@admin_router.post(
    "/economic-data/model-versions",
    response_model=ModelVersionOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une version de modèle (brouillon)",
)
async def create_model_version(
    payload: ModelVersionCreateRequest, service: Service, admin: DataAdmin
) -> ModelVersionOut:
    try:
        version = await service.create_model_version(
            payload.label, payload.dataset_ids, payload.notes, admin.id
        )
    except EconomicDataError as exc:
        raise _http(exc) from exc
    return ModelVersionOut(
        id=version.id,
        label=version.label,
        status=version.status,
        notes=version.notes,
        created_at=version.created_at,
        published_at=version.published_at,
        datasets=[DatasetOut.model_validate(d) for d in version.datasets],
    )


@admin_router.post(
    "/economic-data/publish",
    response_model=ModelVersionOut,
    summary="Publier une version de modèle (irréversible)",
)
async def publish(payload: PublishRequest, service: Service, admin: DataAdmin) -> ModelVersionOut:
    try:
        version = await service.publish(payload.model_version_id, admin.id)
    except EconomicDataError as exc:
        raise _http(exc) from exc
    return ModelVersionOut(
        id=version.id,
        label=version.label,
        status=version.status,
        notes=version.notes,
        created_at=version.created_at,
        published_at=version.published_at,
        datasets=[DatasetOut.model_validate(d) for d in version.datasets],
    )


@admin_router.get(
    "/economic-data/compare",
    response_model=VersionDiffOut,
    summary="Comparer deux versions de modèle",
)
async def compare(from_id: int, to_id: int, service: Service, admin: DataAdmin) -> VersionDiffOut:
    try:
        diff = await service.compare(from_id, to_id)
    except EconomicDataError as exc:
        raise _http(exc) from exc
    return VersionDiffOut(
        added=list(diff.added), removed=list(diff.removed), changed=list(diff.changed)
    )
