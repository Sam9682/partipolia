"""API REST du suivi de mandat (Exigence 21).

Points d'accès :

* ``GET /api/v1/mandate/commitments`` — liste **publique** des Engagements
  (Exigence 21.1) ;
* ``GET /api/v1/mandate/indicators`` — liste **publique** des Indicateurs
  (Exigences 21.3, 21.4) ;
* ``PUT /api/v1/mandate/commitments`` — création/mise à jour d'un Engagement,
  réservée à l'Administrateur (Exigences 21.1, 21.2, 21.4) ;
* ``PATCH /api/v1/mandate/indicators/{indicator_id}`` — mise à jour de la
  ``current_value`` d'un Indicateur, réservée à l'Administrateur (Exigence 21.4).

Les mises à jour du statut d'un Engagement et de la valeur d'un Indicateur sont
consignées dans le Journal_D_Audit par le
:class:`~app.services.mandate_service.MandateService` (Exigence 21.4). Les actions
d'administration sont protégées par :func:`app.api.deps.require_admin`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.mandate import (
    CommitmentInput,
    CommitmentPublic,
    IndicatorPublic,
    IndicatorValueUpdate,
)
from app.services.mandate_service import (
    CommitmentNotFoundError,
    IndicatorNotFoundError,
    InvalidCommitmentStatusError,
    MandateService,
)

router = APIRouter()

# Messages génériques (Exigences 31.1, 31.2).
_COMMITMENT_NOT_FOUND_MESSAGE = "Engagement introuvable."
_INDICATOR_NOT_FOUND_MESSAGE = "Indicateur introuvable."
_INVALID_STATUS_MESSAGE = "Statut d'engagement invalide."


def get_mandate_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MandateService:
    """Dépendance fournissant un :class:`MandateService` lié à la session de requête."""
    return MandateService(session)


@router.get(
    "/commitments",
    response_model=list[CommitmentPublic],
    summary="Liste des Engagements du mandat",
)
async def list_commitments(
    service: Annotated[MandateService, Depends(get_mandate_service)],
) -> list[CommitmentPublic]:
    """Retourne les Engagements du mandat (Exigence 21.1)."""
    commitments = await service.list_commitments()
    return [CommitmentPublic.model_validate(c) for c in commitments]


@router.get(
    "/indicators",
    response_model=list[IndicatorPublic],
    summary="Liste des Indicateurs du mandat",
)
async def list_indicators(
    service: Annotated[MandateService, Depends(get_mandate_service)],
) -> list[IndicatorPublic]:
    """Retourne les Indicateurs du mandat (Exigences 21.3, 21.4)."""
    indicators = await service.list_indicators()
    return [IndicatorPublic.model_validate(i) for i in indicators]


@router.put(
    "/commitments",
    response_model=CommitmentPublic,
    summary="Créer ou mettre à jour un Engagement (Administrateur)",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Statut invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Engagement introuvable"},
    },
)
async def upsert_commitment(
    service: Annotated[MandateService, Depends(get_mandate_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    data: CommitmentInput,
) -> CommitmentPublic:
    """Crée ou met à jour un Engagement ; réservé à l'Administrateur (Exigences 21.1, 21.2, 21.4)."""
    try:
        commitment = await service.upsert_commitment(
            commitment_id=data.id,
            title=data.title,
            status=data.status.value,
            target_date=data.target_date,
            progress=data.progress,
            notes=data.notes,
        )
    except InvalidCommitmentStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_STATUS_MESSAGE,
        ) from exc
    except CommitmentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_COMMITMENT_NOT_FOUND_MESSAGE,
        ) from exc
    return CommitmentPublic.model_validate(commitment)


@router.patch(
    "/indicators/{indicator_id}",
    response_model=IndicatorPublic,
    summary="Mettre à jour la valeur d'un Indicateur (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Indicateur introuvable"},
    },
)
async def update_indicator_value(
    service: Annotated[MandateService, Depends(get_mandate_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    indicator_id: Annotated[int, Path(ge=1, description="Identifiant de l'Indicateur")],
    data: IndicatorValueUpdate,
) -> IndicatorPublic:
    """Met à jour la ``current_value`` d'un Indicateur ; réservé à l'Administrateur (Exigence 21.4)."""
    try:
        indicator = await service.update_indicator(
            indicator_id, current_value=data.current_value
        )
    except IndicatorNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_INDICATOR_NOT_FOUND_MESSAGE,
        ) from exc
    return IndicatorPublic.model_validate(indicator)
