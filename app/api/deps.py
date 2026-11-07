"""Dépendances d'autorisation de l'API (Exigences 1.10, 31.3).

Ce module fournit les dépendances FastAPI qui protègent les points d'entrée
nécessitant une identité :

* :func:`get_access_token` — extrait le JWT d'accès depuis l'en-tête
  ``Authorization: Bearer …`` (clients API) **ou** depuis le cookie HttpOnly
  ``access_token`` (application web, Exigence 1.5) ; en son absence, un ``401``
  ``UNAUTHENTICATED`` à message générique est renvoyé (Exigence 31.3) ;
* :func:`get_current_user` — résout l'``UserPublic`` courant via
  :meth:`AuthService.current_user` ; toute :class:`AuthError` est traduite en
  ``401`` générique, sans divulguer la cause exacte (Exigences 1.9, 31.3). Cette
  garde réserve la création de Propositions, le vote et la publication de
  Commentaires et d'Arguments aux Utilisateurs authentifiés (Exigence 1.10) ;
* :func:`require_admin` — exige ``is_admin`` ; sinon un ``403`` ``FORBIDDEN`` est
  renvoyé (réservation des actions d'administration) ;
* :func:`get_current_user_optional` — variante tolérante retournant ``None`` en
  l'absence d'authentification, pour les pages publiques dont l'affichage varie
  selon l'état de connexion.

Les messages d'erreur d'authentification restent **génériques** : ils
n'indiquent jamais si un compte existe ni pourquoi le jeton est refusé
(Exigences 1.9, 31.3).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import Depends, Request, status
from fastapi.exceptions import HTTPException

from app.core.audit_events import ACTION_ADMIN_ACCESS_DENIED, request_ip_hash
from app.core.database import get_session
from app.services.audit_service import AuditService
from app.models.roles import Role, has_role
from app.services.auth_service import AuthError, AuthService
from app.schemas.auth import UserPublic
from sqlalchemy.ext.asyncio import AsyncSession

# Nom du cookie HttpOnly portant l'access token côté web (Exigence 1.5).
ACCESS_TOKEN_COOKIE_NAME: Final = "access_token"

# Nom du cookie HttpOnly portant le refresh token côté web (Exigence 1.5).
REFRESH_TOKEN_COOKIE_NAME: Final = "refresh_token"

# Préfixe du schéma d'autorisation porteur (OAuth2 « Bearer »).
_BEARER_PREFIX: Final = "Bearer "

# Messages génériques : aucune fuite d'information (Exigences 1.9, 31.3).
_UNAUTHENTICATED_MESSAGE: Final = "Authentification requise."
_FORBIDDEN_MESSAGE: Final = "Action non autorisée."


def _extract_access_token(request: Request) -> str | None:
    """Extrait le token d'accès de l'en-tête ``Authorization`` ou du cookie.

    L'en-tête ``Authorization: Bearer …`` (clients API) est prioritaire ; à
    défaut, le cookie HttpOnly ``access_token`` (application web) est utilisé.
    Retourne ``None`` si aucun jeton exploitable n'est présent.
    """
    authorization = request.headers.get("Authorization")
    if authorization and authorization.startswith(_BEARER_PREFIX):
        token = authorization[len(_BEARER_PREFIX):].strip()
        if token:
            return token

    cookie_token = request.cookies.get(ACCESS_TOKEN_COOKIE_NAME)
    if cookie_token:
        return cookie_token.strip() or None

    return None


def get_access_token(request: Request) -> str:
    """Dépendance renvoyant l'access token courant, sinon ``401`` (Exigence 31.3).

    Le jeton est cherché dans l'en-tête ``Authorization: Bearer`` puis dans le
    cookie ``access_token``. Son absence lève une ``HTTPException`` ``401``
    ``UNAUTHENTICATED`` à message générique.
    """
    token = _extract_access_token(request)
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


async def get_current_user(
    access_token: Annotated[str, Depends(get_access_token)],
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> UserPublic:
    """Résout l'Utilisateur courant à partir de l'access token (Exigences 1.8, 1.10).

    Cette garde réserve aux Utilisateurs authentifiés la création de Propositions,
    le vote et la publication de Commentaires et d'Arguments (Exigence 1.10).
    Toute :class:`AuthError` (jeton invalide, expiré, révoqué, compte inactif)
    est traduite en ``401`` générique sans divulguer la cause (Exigences 1.9, 31.3).
    """
    service = AuthService(session)
    try:
        return await service.current_user(access_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_UNAUTHENTICATED_MESSAGE,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def _audit_admin_denied(request: Request, user: UserPublic) -> None:
    """Consigne un refus 403 d'administration en session isolée (Exigences 15.7, 15.8)."""
    await AuditService(None).record_isolated(  # type: ignore[arg-type]
        action=ACTION_ADMIN_ACCESS_DENIED,
        entity_type="endpoint",
        new_data={"method": request.method, "path": request.url.path},
        ip_hash=request_ip_hash(request),
        actor_id=user.id,
    )


async def require_admin(
    request: Request,
    current_user: Annotated[UserPublic, Depends(get_current_user)],
) -> UserPublic:
    """Exige un Administrateur (``is_admin``), sinon ``403`` ``FORBIDDEN``.

    Les Utilisateurs authentifiés non administrateurs se voient refuser l'accès
    aux actions réservées à l'administration.
    """
    if not current_user.is_admin:
        await _audit_admin_denied(request, current_user)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=_FORBIDDEN_MESSAGE,
        )
    return current_user


def require_roles(*allowed: Role):  # type: ignore[no-untyped-def]
    """Dependency factory: require one of ``allowed`` roles (``super_admin`` always passes).

    Unauthenticated callers receive ``401`` (via :func:`get_current_user`); an
    authenticated caller without a sufficient role receives a generic ``403``.
    """

    async def _guard(
        request: Request,
        current_user: Annotated[UserPublic, Depends(get_current_user)],
    ) -> UserPublic:
        if not has_role(current_user.role, *allowed):
            await _audit_admin_denied(request, current_user)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=_FORBIDDEN_MESSAGE,
            )
        return current_user

    return _guard


async def get_current_user_optional(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> UserPublic | None:
    """Variante optionnelle : retourne l'Utilisateur courant ou ``None``.

    Destinée aux pages publiques dont l'affichage varie selon l'état de
    connexion : ni l'absence de jeton ni un jeton invalide ne provoquent
    d'erreur ; ``None`` est alors renvoyé.
    """
    token = _extract_access_token(request)
    if token is None:
        return None

    service = AuthService(session)
    try:
        return await service.current_user(token)
    except AuthError:
        return None
