"""API REST des Propositions (Exigence 4).

Points d'accès :

* ``POST /api/v1/proposals`` — création d'une Proposition à l'état ``DRAFT``,
  réservée aux Utilisateurs authentifiés (Exigences 4.1, 1.10) → ``201`` ; la
  réponse embarque les éventuelles Propositions proches détectées par similarité
  et les actions Consulter / Créer quand même / Améliorer (Exigence 5), sans
  jamais bloquer la création (Exigence 5.3) ; la validation Pydantic renvoie les
  champs en erreur en cas de corps invalide (Exigences 4.2, 31.4) → ``422`` ;
* ``GET /api/v1/proposals`` — liste **publique** paginée et filtrée par
  ``theme`` / ``status`` / ``sort`` / ``search`` / ``page`` / ``limit``
  (Exigence 4.3) ;
* ``GET /api/v1/proposals/{id}`` — détail **public** d'une Proposition,
  historique compris (Exigence 4.4) → ``404`` si introuvable ;
* ``PUT /api/v1/proposals/{id}`` — modification par l'auteur ou un
  Administrateur : enregistre une nouvelle ``ProposalVersion`` (Exigences 4.5,
  3.4, 3.5, 4.7) → ``200`` ;
* ``DELETE /api/v1/proposals/{id}`` — archivage (statut ``ARCHIVED``) par
  l'auteur ou un Administrateur, en conservant l'historique (Exigences 4.6,
  4.7) → ``200``.

La garde :func:`app.api.deps.get_current_user` réserve la création, la
modification et l'archivage aux Utilisateurs authentifiés (``401`` sinon,
Exigence 1.10) ; le contrôle auteur/Administrateur (``403``) est appliqué par le
:class:`~app.services.proposal_service.ProposalService` (Exigence 4.7). Toute la
logique métier (versionnement, slug, tri stable) est déléguée au Service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.rag.providers import get_embedding_provider
from app.rag.providers.base import EmbeddingProvider
from app.schemas.auth import UserPublic
from app.schemas.duplicate import (
    SIMILAR_PROPOSALS_MESSAGE,
    DuplicateActions,
    ProposalWithDuplicates,
)
from app.schemas.proposal import (
    Page,
    ProposalCreate,
    ProposalDetail,
    ProposalSort,
    ProposalSummary,
    ProposalUpdate,
)
from app.services.duplicate_detection_service import DuplicateDetectionService
from app.services.proposal_service import (
    ProposalNotFoundError,
    ProposalPermissionError,
    ProposalService,
)

router = APIRouter()

# Messages génériques (Exigences 31.2, 31.3).
_PROPOSAL_NOT_FOUND_MESSAGE = "Proposition introuvable."
_FORBIDDEN_MESSAGE = "Action réservée à l'auteur ou à un administrateur."


def get_proposal_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProposalService:
    """Dépendance fournissant un :class:`ProposalService` lié à la session de requête."""
    return ProposalService(session)


def get_duplicate_detection_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> DuplicateDetectionService:
    """Dépendance fournissant le :class:`DuplicateDetectionService` (Exigence 5).

    L'``EmbeddingProvider`` est sélectionné par configuration (Exigence 16.3) et
    injecté afin que le rapprochement par similarité cosinus reste isolé du
    fournisseur concret et testable hors-ligne.
    """
    embedding_provider: EmbeddingProvider = get_embedding_provider()
    return DuplicateDetectionService(session, embedding_provider)


@router.post(
    "",
    response_model=ProposalWithDuplicates,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une Proposition (DRAFT) avec détection de doublons",
    # Limitation de débit du point d'accès sensible de création (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("proposals"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Corps invalide : champs en erreur renvoyés"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def create_proposal(
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    duplicate_service: Annotated[
        DuplicateDetectionService, Depends(get_duplicate_detection_service)
    ],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    data: ProposalCreate,
) -> ProposalWithDuplicates:
    """Crée une Proposition à l'état ``DRAFT`` et signale les doublons (Exigences 4.1, 5).

    Réservé aux Utilisateurs authentifiés ; la validation Pydantic du corps
    renvoie automatiquement les champs en erreur (``422``, Exigences 4.2, 31.4).

    Avant la création, la Plateforme recherche les Propositions proches par
    similarité cosinus (Exigence 5.1). La création **n'est jamais bloquée**
    (« Créer quand même » — Exigence 5.3) : la Proposition est créée dans tous les
    cas. Lorsqu'au moins une Proposition proche est trouvée, la réponse expose la
    liste des similaires, le message « Des propositions similaires existent » et
    les actions Consulter / Créer quand même / Améliorer (Exigence 5.2).
    """
    # Détection AVANT création afin de comparer aux seules Propositions existantes
    # (la nouvelle ne fait pas partie des candidats) — Exigence 5.1.
    similar = await duplicate_service.find_similar(data.title, data.description)

    # Création systématique : la présence de doublons n'empêche pas la création
    # (Exigence 5.3).
    proposal = await service.create(current_user, data)
    detail = ProposalDetail.model_validate(proposal)

    if similar:
        return ProposalWithDuplicates(
            proposal=detail,
            similar=similar,
            message=SIMILAR_PROPOSALS_MESSAGE,
            actions=DuplicateActions(),
        )
    return ProposalWithDuplicates(proposal=detail)


@router.get(
    "",
    response_model=Page[ProposalSummary],
    summary="Lister les Propositions (filtres theme/status/sort/search/page/limit)",
)
async def list_proposals(
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    theme: Annotated[
        str | None, Query(description="Filtre par slug de Thème (Exigence 4.3).")
    ] = None,
    proposal_status: Annotated[
        str | None,
        Query(alias="status", description="Filtre par statut (Exigence 4.3)."),
    ] = None,
    sort: Annotated[
        ProposalSort, Query(description="Tri stable, jamais par nombre de votes (Exigences 4.3, 7.3).")
    ] = "recent",
    search: Annotated[
        str | None, Query(description="Recherche sur le titre (Exigence 4.3).")
    ] = None,
    page: Annotated[int, Query(ge=1, description="Numéro de page (Exigence 4.3).")] = 1,
    limit: Annotated[
        int, Query(ge=1, le=100, description="Taille de page (Exigence 4.3).")
    ] = 20,
) -> Page[ProposalSummary]:
    """Retourne la liste paginée et filtrée des Propositions (Exigence 4.3).

    Consultation **publique** (aucune authentification requise). Le tri ne
    s'appuie jamais sur le nombre de votes (Exigence 7.3).
    """
    return await service.list(
        theme=theme,
        status=proposal_status,
        sort=sort,
        search=search,
        page=page,
        limit=limit,
    )


@router.get(
    "/{proposal_id}",
    response_model=ProposalDetail,
    summary="Détail d'une Proposition",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Proposition introuvable"},
    },
)
async def get_proposal(
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> ProposalDetail:
    """Retourne le détail d'une Proposition, historique compris (Exigence 4.4).

    Consultation **publique** ; ``404`` si la Proposition n'existe pas.
    """
    try:
        return await service.get(proposal_id)
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc


@router.put(
    "/{proposal_id}",
    response_model=ProposalDetail,
    summary="Modifier une Proposition (auteur ou Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {
            "description": "Action réservée à l'auteur ou à un administrateur"
        },
        status.HTTP_404_NOT_FOUND: {"description": "Proposition introuvable"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Corps invalide : champs en erreur renvoyés"
        },
    },
)
async def update_proposal(
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
    data: ProposalUpdate,
) -> ProposalDetail:
    """Modifie une Proposition et crée une nouvelle version (Exigences 4.5, 3.4, 4.7).

    Réservé à l'auteur ou à un Administrateur (``403`` sinon) ; ``404`` si la
    Proposition n'existe pas. La validation Pydantic renvoie les champs en erreur
    (``422``, Exigence 31.4).
    """
    try:
        await service.update(current_user, proposal_id, data)
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    except ProposalPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    # Recharge le détail complet (historique compris) après modification.
    return await service.get(proposal_id)


@router.delete(
    "/{proposal_id}",
    response_model=ProposalDetail,
    summary="Archiver une Proposition (auteur ou Administrateur)",
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {
            "description": "Action réservée à l'auteur ou à un administrateur"
        },
        status.HTTP_404_NOT_FOUND: {"description": "Proposition introuvable"},
    },
)
async def delete_proposal(
    service: Annotated[ProposalService, Depends(get_proposal_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    proposal_id: Annotated[int, Path(ge=1, description="Identifiant de la Proposition")],
) -> ProposalDetail:
    """Archive une Proposition (statut ``ARCHIVED``) sans perdre l'historique.

    Réservé à l'auteur ou à un Administrateur (``403`` sinon, Exigence 4.7) ;
    ``404`` si la Proposition n'existe pas (Exigences 4.6, 4.7). Renvoie le
    détail archivé.
    """
    try:
        await service.archive(current_user, proposal_id)
    except ProposalNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_PROPOSAL_NOT_FOUND_MESSAGE,
        ) from exc
    except ProposalPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        ) from exc
    return await service.get(proposal_id)
