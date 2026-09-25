"""API REST des fonctions RGPD (Exigences 28.4, 28.5, 31.1).

Points d'accès versionnés sous ``/api/v1/privacy``, réservés à l'Utilisateur
authentifié (ils portent sur **ses propres** données) :

* ``GET /privacy/export`` — **export des données** personnelles du compte courant
  (compte, Propositions, Votes, Arguments, Commentaires) (Exigence 28.4) ;
* ``GET /privacy/consents`` / ``PUT /privacy/consents`` — lecture et mise à jour
  de la **gestion du consentement** (Exigence 28.4) ;
* ``DELETE /privacy/account`` — **suppression de compte** : supprime les
  contributions personnelles, anonymise les Propositions rédigées, supprime le
  compte, puis efface les cookies d'authentification (Exigence 28.4) ;
* ``GET /privacy/retention`` — **politique de rétention explicite** (comptes,
  Votes, journaux, conversations IA, signalements ; Exigence 28.5). Ce point est
  public : la politique de rétention doit être consultable sans authentification.

Toute modification d'état non authentifiée est refusée par la garde
``get_current_user`` (Exigence 31.3). Aucune donnée de profilage politique
individuel n'est collectée ni renvoyée (Exigences 28.1, 28.2).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from fastapi.exceptions import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    get_current_user,
)
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.privacy import (
    AccountDeletionResult,
    ConsentState,
    ConsentUpdate,
    DataExport,
    RetentionPolicy,
)
from app.services.privacy_service import PrivacyError, PrivacyService

router = APIRouter()

# Attribut « path » des cookies d'authentification (cf. app/api/v1/auth.py).
_COOKIE_PATH = "/"


def get_privacy_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> PrivacyService:
    """Dépendance fournissant un :class:`PrivacyService` lié à la session de requête."""
    return PrivacyService(session)


@router.get(
    "/retention",
    response_model=RetentionPolicy,
    summary="Politique de rétention explicite (publique)",
)
async def retention() -> RetentionPolicy:
    """Retourne la politique de rétention explicite de la Plateforme (Exigence 28.5)."""
    return PrivacyService.retention_policy()


@router.get(
    "/export",
    response_model=DataExport,
    summary="Export des données personnelles du compte courant",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def export_data(
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    service: Annotated[PrivacyService, Depends(get_privacy_service)],
) -> DataExport:
    """Exporte les données personnelles du compte courant (Exigence 28.4)."""
    try:
        return await service.export_user_data(current_user.id)
    except PrivacyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/consents",
    response_model=ConsentState,
    summary="État du consentement du compte courant",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def get_consents(
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    service: Annotated[PrivacyService, Depends(get_privacy_service)],
) -> ConsentState:
    """Retourne l'état du consentement du compte courant (Exigence 28.4)."""
    try:
        return await service.get_consents(current_user.id)
    except PrivacyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.put(
    "/consents",
    response_model=ConsentState,
    summary="Mise à jour du consentement du compte courant",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def update_consents(
    payload: ConsentUpdate,
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    service: Annotated[PrivacyService, Depends(get_privacy_service)],
) -> ConsentState:
    """Remplace le registre de consentement du compte courant (Exigence 28.4)."""
    try:
        return await service.update_consents(current_user.id, payload.consents)
    except PrivacyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.delete(
    "/account",
    response_model=AccountDeletionResult,
    summary="Suppression du compte courant",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def delete_account(
    response: Response,
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    service: Annotated[PrivacyService, Depends(get_privacy_service)],
) -> AccountDeletionResult:
    """Supprime le compte courant et efface ses cookies d'authentification (Exigence 28.4)."""
    try:
        result = await service.delete_account(current_user.id)
    except PrivacyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    # Efface les cookies côté web : la session ne correspond plus à aucun compte.
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path=_COOKIE_PATH)
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path=_COOKIE_PATH)
    return result
