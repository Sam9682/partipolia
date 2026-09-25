"""API REST du Référentiel_Thématique (Exigence 2).

Points d'accès **publics** (consultation sans authentification) :

* ``GET /api/v1/themes`` — liste des 25 Thèmes (Exigence 2.2) ;
* ``GET /api/v1/themes/{theme_id}`` — détail d'un Thème (Exigence 2.3) ;
* ``GET /api/v1/themes/{theme_id}/proposals`` — Propositions d'un Thème, paginées
  (Exigence 2.4).

Le routeur délègue toute la logique au :class:`~app.services.theme_service.ThemeService`.
Un Thème inexistant se traduit par ``404 NOT_FOUND`` (Exigences 2.3, 2.4, 31.2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.common import Page
from app.schemas.theme import ProposalSummary, ThemePublic
from app.services.theme_service import ThemeNotFoundError, ThemeService

router = APIRouter()

# Message générique « ressource introuvable » (Exigence 31.2).
_THEME_NOT_FOUND_MESSAGE = "Thème introuvable."


def get_theme_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThemeService:
    """Dépendance fournissant un :class:`ThemeService` lié à la session de requête."""
    return ThemeService(session)


@router.get(
    "",
    response_model=list[ThemePublic],
    summary="Liste des 25 Thèmes",
)
async def list_themes(
    service: Annotated[ThemeService, Depends(get_theme_service)],
) -> list[ThemePublic]:
    """Retourne la liste des Thèmes du Référentiel_Thématique (Exigence 2.2)."""
    themes = await service.list_themes()
    return [ThemePublic.model_validate(theme) for theme in themes]


@router.get(
    "/{theme_id}",
    response_model=ThemePublic,
    summary="Détail d'un Thème",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Thème introuvable"}},
)
async def get_theme(
    service: Annotated[ThemeService, Depends(get_theme_service)],
    theme_id: Annotated[int, Path(ge=1, description="Identifiant du Thème")],
) -> ThemePublic:
    """Retourne le détail d'un Thème ; ``404`` s'il n'existe pas (Exigence 2.3)."""
    try:
        theme = await service.get_theme(theme_id)
    except ThemeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_THEME_NOT_FOUND_MESSAGE,
        ) from exc
    return ThemePublic.model_validate(theme)


@router.get(
    "/{theme_id}/proposals",
    response_model=Page[ProposalSummary],
    summary="Propositions rattachées à un Thème",
    responses={status.HTTP_404_NOT_FOUND: {"description": "Thème introuvable"}},
)
async def list_proposals_for_theme(
    service: Annotated[ThemeService, Depends(get_theme_service)],
    theme_id: Annotated[int, Path(ge=1, description="Identifiant du Thème")],
    page: Annotated[int, Query(ge=1, description="Index de page (1-based)")] = 1,
    limit: Annotated[int, Query(ge=1, le=100, description="Taille de page")] = 20,
) -> Page[ProposalSummary]:
    """Retourne les Propositions d'un Thème, paginées ; ``404`` si le Thème n'existe pas (Exigence 2.4)."""
    try:
        proposals, total = await service.list_proposals_for_theme(
            theme_id, page=page, limit=limit
        )
    except ThemeNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_THEME_NOT_FOUND_MESSAGE,
        ) from exc

    items = [ProposalSummary.model_validate(proposal) for proposal in proposals]
    return Page.create(items, total=total, page=page, limit=limit)
