"""API REST des Arguments (Exigence 8).

Points d'accès :

* ``POST /api/v1/proposals/{proposal_id}/arguments`` — création d'un Argument
  ``FOR``/``AGAINST`` sur une Proposition, réservée aux Utilisateurs authentifiés
  (Exigences 8.1, 8.2, 1.10) → ``201`` ;
* ``PUT /api/v1/arguments/{argument_id}`` — modification du ``content`` d'un
  Argument par son auteur ou un Administrateur (Exigences 8.2, 8.3) → ``200`` ;
* ``DELETE /api/v1/arguments/{argument_id}`` — suppression d'un Argument par son
  auteur ou un Administrateur (Exigences 8.2, 8.3) → ``204``.

Deux routeurs sont exposés : :data:`router` pour les points ``/arguments`` et
:data:`proposal_arguments_router` pour le point rattaché aux Propositions. La
garde :func:`app.api.deps.get_current_user` réserve la création, la modification
et la suppression aux Utilisateurs authentifiés (``401`` sinon, Exigence 1.10) ;
le contrôle auteur/Administrateur (``403``) est appliqué par le
:class:`~app.services.argument_service.ArgumentService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.schemas.argument import ArgumentCreate, ArgumentPublic, ArgumentUpdate
from app.schemas.auth import UserPublic
from app.services.argument_service import (
    ArgumentNotFoundError,
    ArgumentPermissionError,
    ArgumentService,
    InvalidArgumentPositionError,
    ProposalNotFoundError,
)

router = APIRouter()
proposal_arguments_router = APIRouter()

# Messages génériques (Exigences 31.2, 31.3).
_PROPOSAL_NOT_FOUND_MESSAGE = "Proposition introuvable."
_ARGUMENT_NOT_FOUND_MESSAGE = "Argument introuvable."
_INVALID_POSITION_MESSAGE = "La position doit être FOR ou AGAINST."
_FORBIDDEN_MESSAGE = "Action réservée à l'auteur ou à un administrateur."


def get_argument_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ArgumentService:
    """Dépendance fournissant un :class:`ArgumentService` lié à la session de requête."""
    return ArgumentService(session)


@proposal_arguments_router.post(
    "/{proposal_id}/arguments",
    response_model=ArgumentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un Argument FOR/AGAINST sur une Proposition",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Requête invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {"description": "Proposition introuvable"},
    },
)
async def create_argument(
    service: Annotated[ArgumentService, Depends(get_argument_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
    data: ArgumentCreate,
) -> ArgumentPublic:
    """Crée un Argument sur une Proposition (Exigences 8.1, 8.2).

    Réservé aux Utilisateurs authentifiés (Exigence 1.10) ; ``404`` si la
    Proposition n'existe pas, ``400`` si la position est invalide.
    """
    try:
        argument = await service.create(
            current_user, proposal_id, data.position.value, data.content
        )
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    except InvalidArgumentPositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_POSITION_MESSAGE,
        ) from exc
    return ArgumentPublic.model_validate(argument)


@router.put(
    "/{argument_id}",
    response_model=ArgumentPublic,
    summary="Modifier un Argument (auteur ou Administrateur)",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Requête invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'auteur ou à un administrateur"},
        status.HTTP_404_NOT_FOUND: {"description": "Argument introuvable"},
    },
)
async def update_argument(
    service: Annotated[ArgumentService, Depends(get_argument_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    argument_id: Annotated[int, Path(ge=1, description="Identifiant de l'Argument")],
    data: ArgumentUpdate,
) -> ArgumentPublic:
    """Modifie le ``content`` d'un Argument (Exigences 8.2, 8.3).

    Réservé à l'auteur ou à un Administrateur (``403`` sinon) ; ``404`` si
    l'Argument n'existe pas.
    """
    try:
        argument = await service.update(current_user, argument_id, data.content)
    except ArgumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_ARGUMENT_NOT_FOUND_MESSAGE,
        ) from exc
    except ArgumentPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    return ArgumentPublic.model_validate(argument)


@router.delete(
    "/{argument_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un Argument (auteur ou Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'auteur ou à un administrateur"},
        status.HTTP_404_NOT_FOUND: {"description": "Argument introuvable"},
    },
)
async def delete_argument(
    service: Annotated[ArgumentService, Depends(get_argument_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    argument_id: Annotated[int, Path(ge=1, description="Identifiant de l'Argument")],
) -> Response:
    """Supprime un Argument (Exigences 8.2, 8.3).

    Réservé à l'auteur ou à un Administrateur (``403`` sinon) ; ``404`` si
    l'Argument n'existe pas. Renvoie ``204 No Content`` en cas de succès.
    """
    try:
        await service.delete(current_user, argument_id)
    except ArgumentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_ARGUMENT_NOT_FOUND_MESSAGE,
        ) from exc
    except ArgumentPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
