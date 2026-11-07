"""National dashboard endpoints (public, aggregated, privacy-preserving)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.audit_events import request_ip_hash
from app.core.database import get_session
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.dashboard import (
    DashboardHistory,
    DashboardNational,
    DashboardSectors,
    RegionalHistory,
    RegionalList,
    RegionalView,
)
from app.services.aggregation_service import AggregationService
from app.services.regional_aggregation_service import (
    RegionalAggregationService,
    RegionNotFoundError,
    RegionWithheldError,
)
from app.services.simulation_service import DatabaseSnapshotProvider

router = APIRouter()
admin_router = APIRouter()

Session = Annotated[AsyncSession, Depends(get_session, scope="function")]


@router.get("/national", response_model=DashboardNational, summary="Impact national agrégé")
async def national(session: Session) -> DashboardNational:
    return await AggregationService(session).national()


@router.get("/history", response_model=DashboardHistory, summary="Historique de l'impact agrégé")
async def history(session: Session) -> DashboardHistory:
    return await AggregationService(session).history()


@router.get("/sectors", response_model=DashboardSectors, summary="Secteurs les plus touchés")
async def sectors(session: Session) -> DashboardSectors:
    return await AggregationService(session).sectors()


@router.get("/regions", response_model=RegionalList, summary="Impact agrégé par Région")
async def regions(session: Session) -> RegionalList:
    return await RegionalAggregationService(session).list_regions()


@router.get(
    "/regions/{code}/history",
    response_model=RegionalHistory,
    summary="Historique d'une Région",
)
async def region_history(code: str, session: Session) -> RegionalHistory:
    try:
        return await RegionalAggregationService(session).history(code)
    except RegionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Région inconnue.") from exc


@router.get("/regions/{code}", response_model=RegionalView, summary="Détail d'une Région")
async def region_detail(code: str, session: Session) -> RegionalView:
    try:
        return await RegionalAggregationService(session).detail(code)
    except RegionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Région inconnue.") from exc


@admin_router.get(
    "/dashboard/regions/{code}",
    response_model=RegionalView,
    summary="Détail d'une Région (super administrateur)",
)
async def admin_region_detail(
    code: str,
    request: Request,
    session: Session,
    admin: Annotated[UserPublic, Depends(require_roles(Role.SUPER_ADMIN))],
) -> RegionalView:
    try:
        return await RegionalAggregationService(session).admin_detail(
            code,
            admin,  # type: ignore[arg-type]  # only ``id`` is used
            request_ip_hash(request),
        )
    except RegionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Région inconnue.") from exc
    except RegionWithheldError as exc:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Données de la Région non publiables."
        ) from exc


@admin_router.post(
    "/dashboard/refresh",
    response_model=DashboardNational,
    summary="Recalculer l'agrégat national (administrateur de données)",
)
async def refresh(
    session: Session,
    _admin: Annotated[UserPublic, Depends(require_roles(Role.DATA_ADMIN))],
) -> DashboardNational:
    provider = DatabaseSnapshotProvider(session)
    snapshot = await provider.get_snapshot()
    service = AggregationService(session)
    await service.refresh(snapshot)
    return await service.national()
