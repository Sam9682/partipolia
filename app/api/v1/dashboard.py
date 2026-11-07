"""National dashboard endpoints (public, aggregated, privacy-preserving)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_session
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.dashboard import DashboardHistory, DashboardNational, DashboardSectors
from app.services.aggregation_service import AggregationService
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
