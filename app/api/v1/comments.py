"""API REST des Commentaires (Exigence 9).

Points d'accès :

* ``GET /api/v1/proposals/{proposal_id}/comments`` — fils de discussion d'une
  Proposition (consultation publique, Exigences 9.2, 9.3) → ``200`` ;
* ``POST /api/v1/proposals/{proposal_id}/comments`` — publication d'un
  Commentaire réservée aux Utilisateurs authentifiés (Exigences 9.1, 9.5, 1.10),
  soumis au Moteur_De_Modération (Exigence 17.x) → ``201`` ;
* ``PUT /api/v1/comments/{comment_id}`` — modification du ``content`` par son
  auteur ou un Administrateur (Exigence 9.3) → ``200`` ;
* ``DELETE /api/v1/comments/{comment_id}`` — suppression par son auteur ou un
  Administrateur (Exigence 9.3) → ``204``.

Deux routeurs sont exposés : :data:`router` pour les points ``/comments`` et
:data:`proposal_comments_router` pour les points rattachés aux Propositions. La
garde :func:`app.api.deps.get_current_user` réserve la création, la modification
et la suppression aux Utilisateurs authentifiés (``401`` sinon, Exigences 1.10,
9.5) ; le contrôle auteur/Administrateur (``403``) est appliqué par le
:class:`~app.services.comment_service.CommentService`. La lecture des fils reste
publique.
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
from app.schemas.comment import (
    CommentCreate,
    CommentNode,
    CommentPublic,
    CommentSubmission,
    CommentUpdate,
)
from app.services.comment_service import (
    CommentNotFoundError,
    CommentPermissionError,
    CommentService,
    ParentCommentNotFoundError,
    ProposalNotFoundError,
)

router = APIRouter()
proposal_comments_router = APIRouter()

# Messages génériques (Exigences 31.2, 31.3).
_PROPOSAL_NOT_FOUND_MESSAGE = "Proposition introuvable."
_PARENT_NOT_FOUND_MESSAGE = "Commentaire parent introuvable."
_COMMENT_NOT_FOUND_MESSAGE = "Commentaire introuvable."
_FORBIDDEN_MESSAGE = "Action réservée à l'auteur ou à un administrateur."


def get_comment_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> CommentService:
    """Dépendance fournissant un :class:`CommentService` lié à la session de requête."""
    return CommentService(session)


@proposal_comments_router.get(
    "/{proposal_id}/comments",
    response_model=list[CommentNode],
    summary="Lister les fils de discussion d'une Proposition",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Proposition introuvable"},
    },
)
async def list_comments(
    service: Annotated[CommentService, Depends(get_comment_service)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> list[CommentNode]:
    """Reconstitue les fils de discussion d'une Proposition (Exigence 9.2).

    Consultation publique : aucune authentification requise (Exigence 9.3).
    """
    return await service.list_thread(proposal_id)


@proposal_comments_router.post(
    "/{proposal_id}/comments",
    response_model=CommentSubmission,
    status_code=status.HTTP_201_CREATED,
    summary="Publier un Commentaire sur une Proposition (→ modération)",
    # Limitation de débit du point d'accès sensible de publication (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("comments"))],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Requête invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {"description": "Proposition ou parent introuvable"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def create_comment(
    service: Annotated[CommentService, Depends(get_comment_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
    data: CommentCreate,
) -> CommentSubmission:
    """Publie un Commentaire et le soumet au Moteur_De_Modération (Exigences 9.1, 9.4).

    Réservé aux Utilisateurs authentifiés (Exigences 1.10, 9.5) ; ``404`` si la
    Proposition ou le Commentaire parent n'existe pas. Le statut résultant
    (``VISIBLE`` ou ``PENDING``) est porté par la décision de modération.
    """
    try:
        submission = await service.create(
            current_user, proposal_id, data.content, data.parent_id
        )
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    except ParentCommentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PARENT_NOT_FOUND_MESSAGE,
        ) from exc
    return submission


@router.put(
    "/{comment_id}",
    response_model=CommentPublic,
    summary="Modifier un Commentaire (auteur ou Administrateur)",
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Requête invalide"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'auteur ou à un administrateur"},
        status.HTTP_404_NOT_FOUND: {"description": "Commentaire introuvable"},
    },
)
async def update_comment(
    service: Annotated[CommentService, Depends(get_comment_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    comment_id: Annotated[int, Path(ge=1, description="Identifiant du Commentaire")],
    data: CommentUpdate,
) -> CommentPublic:
    """Modifie le ``content`` d'un Commentaire (Exigence 9.3).

    Réservé à l'auteur ou à un Administrateur (``403`` sinon) ; ``404`` si le
    Commentaire n'existe pas.
    """
    try:
        comment = await service.update(current_user, comment_id, data.content)
    except CommentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_COMMENT_NOT_FOUND_MESSAGE,
        ) from exc
    except CommentPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    return CommentPublic.model_validate(comment)


@router.delete(
    "/{comment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un Commentaire (auteur ou Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'auteur ou à un administrateur"},
        status.HTTP_404_NOT_FOUND: {"description": "Commentaire introuvable"},
    },
)
async def delete_comment(
    service: Annotated[CommentService, Depends(get_comment_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    comment_id: Annotated[int, Path(ge=1, description="Identifiant du Commentaire")],
) -> Response:
    """Supprime un Commentaire (Exigence 9.3).

    Réservé à l'auteur ou à un Administrateur (``403`` sinon) ; ``404`` si le
    Commentaire n'existe pas. Renvoie ``204 No Content`` en cas de succès.
    """
    try:
        await service.delete(current_user, comment_id)
    except CommentNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_COMMENT_NOT_FOUND_MESSAGE,
        ) from exc
    except CommentPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
