"""API REST du Programme (Exigences 18 et 19).

Points d'accès :

* ``GET /api/v1/program`` — Programme (**public**) (Exigence 18.1) ;
* ``GET /api/v1/program/proposals`` — Propositions du Programme (**public**),
  ordonnées par ``priority`` (Exigences 18.1, 18.4) ;
* ``POST /api/v1/program/proposals`` — ajout d'une Proposition au Programme
  (``priority`` + ``included_at``), réservé à l'**Administrateur** (Exigences
  18.2, 18.4) ;
* ``DELETE /api/v1/program/proposals/{id}`` — retrait de l'association, réservé à
  l'**Administrateur** (Exigence 18.3) ;
* ``GET /api/v1/program/statistics`` — statistiques agrégées du Programme
  (**public**) (Exigence 18.5).

La vue « Programme en construction » (Exigence 19) est un calcul de restitution
non décisionnel exposé par :meth:`ProgramService.build_draft_view` ; l'inclusion
définitive reste un acte d'administration explicite via ``POST`` (Exigence 19.3).
Les actions d'administration sont protégées par :func:`app.api.deps.require_admin`.
Le routeur délègue toute la logique au
:class:`~app.services.program_service.ProgramService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.program import (
    ProgramProposalAdd,
    ProgramProposalPublic,
    ProgramPublic,
    ProgramStatistics,
)
from app.services.program_service import (
    ProgramNotFoundError,
    ProgramProposalNotFoundError,
    ProgramService,
    ProposalNotFoundError,
)

router = APIRouter()

# Messages génériques « ressource introuvable » (Exigence 31.2).
_PROGRAM_NOT_FOUND_MESSAGE = "Programme introuvable."
_PROPOSAL_NOT_FOUND_MESSAGE = "Proposition introuvable."
_ASSOCIATION_NOT_FOUND_MESSAGE = "Association Programme_Proposition introuvable."


def get_program_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProgramService:
    """Dépendance fournissant un :class:`ProgramService` lié à la session de requête."""
    return ProgramService(session)


@router.get(
    "",
    response_model=ProgramPublic,
    summary="Programme",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Programme introuvable"}},
)
async def get_program(
    service: Annotated[ProgramService, Depends(get_program_service)],
) -> ProgramPublic:
    """Retourne le Programme (Exigence 18.1). Consultation publique."""
    try:
        program = await service.get_program()
    except ProgramNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROGRAM_NOT_FOUND_MESSAGE,
        ) from exc
    return ProgramPublic.model_validate(program)


@router.get(
    "/proposals",
    response_model=list[ProgramProposalPublic],
    summary="Propositions du Programme",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Programme introuvable"}},
)
async def list_program_proposals(
    service: Annotated[ProgramService, Depends(get_program_service)],
) -> list[ProgramProposalPublic]:
    """Liste les Propositions du Programme, ordonnées par ``priority`` (Exigences 18.1, 18.4).

    Consultation publique. ``priority`` et ``included_at`` sont des attributs de
    l'association (Exigence 18.4).
    """
    try:
        associations = await service.list_program_proposals()
    except ProgramNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROGRAM_NOT_FOUND_MESSAGE,
        ) from exc
    return [ProgramProposalPublic.model_validate(assoc) for assoc in associations]


@router.post(
    "/proposals",
    response_model=ProgramProposalPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Ajouter une Proposition au Programme (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Programme ou Proposition introuvable"},
    },
)
async def add_program_proposal(
    service: Annotated[ProgramService, Depends(get_program_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    data: ProgramProposalAdd,
) -> ProgramProposalPublic:
    """Ajoute une Proposition au Programme (``priority`` + ``included_at``) (Exigences 18.2, 18.4).

    Réservé à l'Administrateur ; l'inclusion est explicite et traçable
    (Exigence 19.3). ``404`` si le Programme ou la Proposition n'existe pas.
    """
    try:
        association = await service.add_proposal(data.proposal_id, data.priority)
    except ProgramNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROGRAM_NOT_FOUND_MESSAGE,
        ) from exc
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    return ProgramProposalPublic.model_validate(association)


@router.delete(
    "/proposals/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirer une Proposition du Programme (Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_404_NOT_FOUND: {"description": "Association introuvable"},
    },
)
async def remove_program_proposal(
    service: Annotated[ProgramService, Depends(get_program_service)],
    _admin: Annotated[UserPublic, Depends(require_admin)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> Response:
    """Retire l'association Programme_Proposition (Exigence 18.3).

    Réservé à l'Administrateur ; ``404`` si l'association n'existe pas.
    """
    try:
        await service.remove_proposal(proposal_id)
    except ProgramNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROGRAM_NOT_FOUND_MESSAGE,
        ) from exc
    except ProgramProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_ASSOCIATION_NOT_FOUND_MESSAGE,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/statistics",
    response_model=ProgramStatistics,
    summary="Statistiques du Programme",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Programme introuvable"}},
)
async def get_program_statistics(
    service: Annotated[ProgramService, Depends(get_program_service)],
) -> ProgramStatistics:
    """Retourne les statistiques agrégées et anonymisées du Programme (Exigence 18.5). Publique."""
    try:
        return await service.statistics()
    except ProgramNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROGRAM_NOT_FOUND_MESSAGE,
        ) from exc
