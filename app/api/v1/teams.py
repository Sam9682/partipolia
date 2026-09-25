"""API REST des Équipes (Exigence 20).

Points d'accès :

* ``GET /api/v1/teams`` — liste **publique** des Équipes et de leurs membres
  (Exigences 20.1, 20.2) ;
* ``POST /api/v1/teams`` — création d'une Équipe, réservée à l'Administrateur
  (Exigence 20.3) ;
* ``POST /api/v1/teams/{team_id}/members`` — ajout d'un membre (``role``, ``bio``),
  réservé à l'Administrateur (Exigences 20.2, 20.3) ;
* ``DELETE /api/v1/teams/members/{member_id}`` — retrait d'un membre, réservé à
  l'Administrateur (Exigence 20.3).

Les actions d'administration (création/modification/suppression) sont protégées
par :func:`app.api.deps.require_admin` et consignées dans le Journal_D_Audit par
le :class:`~app.services.team_service.TeamService` (Exigence 20.3). Le routeur
délègue toute la logique au Service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.team import (
    TeamCreate,
    TeamMemberCreate,
    TeamMemberPublic,
    TeamPublic,
)
from app.services.team_service import (
    TeamMemberNotFoundError,
    TeamNotFoundError,
    TeamService,
)

router = APIRouter()

# Messages génériques « ressource introuvable » (Exigence 31.2).
_TEAM_NOT_FOUND_MESSAGE = "Équipe introuvable."
_TEAM_MEMBER_NOT_FOUND_MESSAGE = "Membre d'Équipe introuvable."


def get_team_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TeamService:
    """Dépendance fournissant un :class:`TeamService` lié à la session de requête."""
    return TeamService(session)


@router.get(
    "",
    response_model=list[TeamPublic],
    summary="Liste des Équipes et de leurs membres",
)
async def list_teams(
    service: Annotated[TeamService, Depends(get_team_service)],
) -> list[TeamPublic]:
    """Retourne les Équipes avec leurs membres (Exigences 20.1, 20.2)."""
    teams = await service.list_teams()
    return [TeamPublic.model_validate(team) for team in teams]


@router.post(
    "",
    response_model=TeamPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une Équipe (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
    },
)
async def create_team(
    service: Annotated[TeamService, Depends(get_team_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    data: TeamCreate,
) -> TeamPublic:
    """Crée une Équipe ; réservé à l'Administrateur (Exigence 20.3)."""
    team = await service.create_team(data.name)
    return TeamPublic.model_validate(team)


@router.post(
    "/{team_id}/members",
    response_model=TeamMemberPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter un membre à une Équipe (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Équipe introuvable"},
    },
)
async def add_team_member(
    service: Annotated[TeamService, Depends(get_team_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    team_id: Annotated[int, Path(ge=1, description="Identifiant de l'Équipe")],
    data: TeamMemberCreate,
) -> TeamMemberPublic:
    """Ajoute un membre (``role``, ``bio``) à une Équipe (Exigences 20.2, 20.3).

    Réservé à l'Administrateur ; ``404`` si l'Équipe n'existe pas.
    """
    try:
        member = await service.add_member(
            team_id, user_id=data.user_id, role=data.role, bio=data.bio
        )
    except TeamNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_TEAM_NOT_FOUND_MESSAGE,
        ) from exc
    return TeamMemberPublic.model_validate(member)


@router.delete(
    "/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirer un membre d'une Équipe (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Membre d'Équipe introuvable"},
    },
)
async def remove_team_member(
    service: Annotated[TeamService, Depends(get_team_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    member_id: Annotated[int, Path(ge=1, description="Identifiant du membre")],
) -> Response:
    """Retire un membre d'une Équipe ; réservé à l'Administrateur (Exigence 20.3)."""
    try:
        await service.remove_member(member_id)
    except TeamMemberNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_TEAM_MEMBER_NOT_FOUND_MESSAGE,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
