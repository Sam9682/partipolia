"""Simulation endpoints (anonymous access allowed, rate limited)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import UserPublic
from app.schemas.simulation import SimulationRequest, SimulationResponse
from app.services.simulation_service import (
    DatabaseSnapshotProvider,
    SimulationForbiddenError,
    SimulationNotFoundError,
    SimulationService,
    UnknownCategoryError,
)

router = APIRouter()


def get_simulation_service(
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> SimulationService:
    """Dependency providing the simulation service (overridable in tests)."""
    return SimulationService(DatabaseSnapshotProvider(session), session)


@router.post(
    "/calculate",
    response_model=SimulationResponse,
    summary="Calculer l'impact d'un plan de réduction de consommation",
    dependencies=[Depends(default_rate_limit("simulate"))],
    responses={status.HTTP_400_BAD_REQUEST: {"description": "Entrée invalide"}},
)
async def calculate(
    payload: SimulationRequest,
    service: Annotated[SimulationService, Depends(get_simulation_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> SimulationResponse:
    """Personal simulation; no account required.

    Nothing is stored unless ``save`` is true; only the inputs are kept, never any
    personal consumption data.
    """
    try:
        return await service.calculate(payload, current_user.id if current_user else None)
    except UnknownCategoryError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Catégorie inconnue : {exc}",
        ) from exc


@router.get(
    "/{simulation_id}",
    response_model=SimulationResponse,
    summary="Relire une simulation enregistrée (recalculée avec sa version de modèle)",
    dependencies=[Depends(default_rate_limit("simulate"))],
)
async def get_simulation(
    simulation_id: int,
    service: Annotated[SimulationService, Depends(get_simulation_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> SimulationResponse:
    try:
        return await service.get(simulation_id, current_user.id if current_user else None)
    except SimulationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation introuvable.") from exc
    except SimulationForbiddenError as exc:
        # Same answer as "not found": do not reveal that the simulation exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Simulation introuvable.") from exc
