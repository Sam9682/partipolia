"""API REST des Votes (Exigence 6).

Points d'accès (tous rattachés à une Proposition) :

* ``POST /api/v1/proposals/{proposal_id}/votes`` — enregistre ou met à jour
  (UPSERT) le Vote de l'Utilisateur authentifié avec un corps ``{value}`` où
  ``value ∈ {+1, 0, -1}`` ; renvoie les décomptes agrégés (Exigences 6.1, 6.2,
  6.3) → ``200`` ;
* ``DELETE /api/v1/proposals/{proposal_id}/votes`` — retire le Vote de
  l'Utilisateur authentifié et l'exclut des décomptes (Exigence 6.4) → ``204`` ;
* ``GET /api/v1/proposals/{proposal_id}/votes`` — expose les décomptes de Votes
  d'une Proposition ; consultation **publique** (Exigence 6.5) → ``200``.

La garde :func:`app.api.deps.get_current_user` réserve ``POST`` et ``DELETE`` aux
Utilisateurs authentifiés : une tentative anonyme reçoit un ``401`` invitant à
l'authentification (Exigence 6.6). Le ``GET`` des décomptes reste public,
conformément au principe de décomptes explicites consultables sans compte
(Exigence 7.4). Le routeur délègue toute la logique métier au
:class:`~app.services.vote_service.VoteService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Response, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import UserPublic
from app.schemas.vote import VoteCounts, VoteRequest
from app.services.vote_service import VoteError, VoteService

# Ce routeur est rattaché aux Propositions (``/proposals/{id}/votes``) ; il est
# monté par ``app.api.v1.router`` sous le préfixe ``/proposals``.
router = APIRouter()

# Message générique pour une valeur de vote hors du domaine ``{+1, 0, -1}``.
_INVALID_VOTE_VALUE_MESSAGE = "La valeur du vote doit être +1, 0 ou -1."


def get_vote_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> VoteService:
    """Dépendance fournissant un :class:`VoteService` lié à la session de requête."""
    return VoteService(session)


@router.post(
    "/{proposal_id}/votes",
    response_model=VoteCounts,
    status_code=status.HTTP_200_OK,
    summary="Voter sur une Proposition (UPSERT +1/0/-1)",
    # Limitation de débit du point d'accès sensible de vote (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("votes"))],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Valeur de vote invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def cast_vote(
    service: Annotated[VoteService, Depends(get_vote_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
    data: VoteRequest,
) -> VoteCounts:
    """Enregistre ou met à jour le Vote de l'Utilisateur (Exigences 6.1, 6.2, 6.3).

    Un UPSERT sur ``UNIQUE(proposal_id, user_id)`` garantit un Vote unique par
    Proposition : un revote remplace la valeur sans créer de ligne
    supplémentaire. Réservé aux Utilisateurs authentifiés (``401`` sinon,
    Exigence 6.6) ; ``400`` si la valeur est hors ``{+1, 0, -1}``. Renvoie les
    décomptes agrégés à jour.
    """
    try:
        return await service.cast(current_user, proposal_id, data.value)
    except VoteError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=_INVALID_VOTE_VALUE_MESSAGE,
        ) from exc


@router.delete(
    "/{proposal_id}/votes",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Retirer son Vote d'une Proposition",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
    },
)
async def withdraw_vote(
    service: Annotated[VoteService, Depends(get_vote_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> Response:
    """Retire le Vote de l'Utilisateur sur la Proposition (Exigence 6.4).

    Le Vote retiré est exclu des décomptes. L'opération est idempotente : retirer
    un Vote inexistant ne provoque pas d'erreur. Réservé aux Utilisateurs
    authentifiés (``401`` sinon, Exigence 6.6). Renvoie ``204 No Content``.
    """
    await service.withdraw(current_user, proposal_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/{proposal_id}/votes",
    response_model=VoteCounts,
    summary="Décomptes de Votes d'une Proposition (public)",
)
async def get_vote_counts(
    service: Annotated[VoteService, Depends(get_vote_service)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> VoteCounts:
    """Retourne les décomptes agrégés des Votes d'une Proposition (Exigence 6.5).

    Consultation **publique** : aucun compte n'est requis, conformément au
    principe de décomptes explicites (Exigence 7.4).
    """
    return await service.counts(proposal_id)
