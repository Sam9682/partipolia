"""API REST des Sources documentaires (Exigence 10).

Points d'accès :

* ``GET /api/v1/sources`` — liste **publique** des Sources, filtrable par
  ``source_type`` (Exigences 10.1, 10.2) ;
* ``POST /api/v1/sources`` — création d'une Source, réservée à l'Administrateur
  (Exigences 10.1, 10.2, 10.4) ;
* ``POST /api/v1/proposals/{proposal_id}/sources`` — rattachement d'une Source à
  une Proposition avec un ``relevance_score``, réservé à l'Administrateur
  (Exigences 10.3, 10.4).

Deux routeurs sont exposés : :data:`router` pour les points ``/sources`` et
:data:`proposal_sources_router` pour le point rattaché aux Propositions. Les
actions d'administration sont protégées par :func:`app.api.deps.require_admin`.
Le routeur délègue toute la logique au
:class:`~app.services.source_service.SourceService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.source import (
    ProposalSourceAttach,
    ProposalSourcePublic,
    SourceCreate,
    SourcePublic,
    SourceType,
)
from app.services.source_service import (
    InvalidSourceTypeError,
    ProposalNotFoundError,
    SourceNotFoundError,
    SourceService,
)

router = APIRouter()
proposal_sources_router = APIRouter()

# Message générique « ressource introuvable » (Exigence 31.2).
_PROPOSAL_NOT_FOUND_MESSAGE = "Proposition introuvable."
_SOURCE_NOT_FOUND_MESSAGE = "Source introuvable."
_INVALID_SOURCE_TYPE_MESSAGE = "Type de source invalide."


def get_source_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> SourceService:
    """Dépendance fournissant un :class:`SourceService` lié à la session de requête."""
    return SourceService(session)


@router.get(
    "",
    response_model=list[SourcePublic],
    summary="Liste des Sources documentaires",
)
async def list_sources(
    service: Annotated[SourceService, Depends(get_source_service)],
    source_type: Annotated[
        SourceType | None,
        Query(description="Filtre optionnel sur le type de source (Exigence 10.2)."),
    ] = None,
) -> list[SourcePublic]:
    """Retourne la liste des Sources, filtrable par ``source_type`` (Exigences 10.1, 10.2)."""
    try:
        sources = await service.list_sources(
            source_type=source_type.value if source_type is not None else None
        )
    except InvalidSourceTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_SOURCE_TYPE_MESSAGE,
        ) from exc
    return [SourcePublic.model_validate(source) for source in sources]


@router.post(
    "",
    response_model=SourcePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une Source (Administrateur)",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Requête invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
    },
)
async def create_source(
    service: Annotated[SourceService, Depends(get_source_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    data: SourceCreate,
) -> SourcePublic:
    """Crée une Source ; réservé à l'Administrateur (Exigences 10.1, 10.2, 10.4)."""
    try:
        source = await service.create(data)
    except InvalidSourceTypeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_SOURCE_TYPE_MESSAGE,
        ) from exc
    return SourcePublic.model_validate(source)


@proposal_sources_router.post(
    "/{proposal_id}/sources",
    response_model=ProposalSourcePublic,
    status_code=status.HTTP_201_CREATED,
    summary="Rattacher une Source à une Proposition (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Proposition ou Source introuvable"},
    },
)
async def attach_source_to_proposal(
    service: Annotated[SourceService, Depends(get_source_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
    data: ProposalSourceAttach,
) -> ProposalSourcePublic:
    """Rattache une Source à une Proposition avec un ``relevance_score`` (Exigences 10.3, 10.4).

    Réservé à l'Administrateur ; ``404`` si la Proposition ou la Source n'existe pas.
    """
    try:
        link = await service.attach_to_proposal(proposal_id, data)
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    except SourceNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_SOURCE_NOT_FOUND_MESSAGE,
        ) from exc
    return ProposalSourcePublic.model_validate(link)
