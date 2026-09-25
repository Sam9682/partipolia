"""API REST d'authentification (Exigence 1, Exigence 31.1).

Points d'accès versionnés sous ``/api/v1/auth`` :

* ``POST /auth/register`` — inscription ; email déjà connu ⇒ ``400`` à message
  **générique** ne révélant pas l'existence du compte (Exigences 1.1, 1.2) ;
* ``POST /auth/login`` — émet un couple access + refresh (Exigence 1.4) et, pour
  l'application web, dépose ces jetons dans des cookies **HttpOnly / Secure /
  SameSite=Lax** (Exigence 1.5) ; identifiants invalides ⇒ ``401`` générique
  (Exigence 1.9) ;
* ``POST /auth/refresh`` — nouveau access token à partir d'un refresh token
  valide, lu du corps de requête **ou** du cookie ``refresh_token`` (Exigence 1.6) ;
* ``POST /auth/logout`` — invalide la session courante et efface les cookies
  (Exigence 1.7) ;
* ``GET /auth/me`` — compte courant sans ``password_hash`` (Exigence 1.8).

Le routeur délègue toute la logique métier à l':class:`~app.services.auth_service.AuthService`.
Les messages d'erreur d'inscription et de connexion restent volontairement
génériques (Exigences 1.2, 1.9).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.exceptions import HTTPException
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    ACCESS_TOKEN_COOKIE_NAME,
    REFRESH_TOKEN_COOKIE_NAME,
    get_access_token,
    get_current_user,
)
from app.core.config import settings
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import (
    AccessToken,
    LoginRequest,
    RegisterRequest,
    TokenPair,
    UserPublic,
)
from app.services.auth_service import (
    GENERIC_CREDENTIALS_ERROR,
    AuthError,
    AuthService,
)

router = APIRouter()

# Attribut « path » des cookies : ils accompagnent l'ensemble de l'application.
_COOKIE_PATH = "/"
# SameSite=Lax : compromis entre protection CSRF et navigation web (Exigence 1.5).
_COOKIE_SAMESITE = "lax"


def get_auth_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthService:
    """Dépendance fournissant un :class:`AuthService` lié à la session de requête."""
    return AuthService(session)


def _cookies_are_secure() -> bool:
    """Indique si les cookies doivent porter l'attribut ``Secure`` (Exigence 1.5).

    L'attribut ``Secure`` est activé hors développement local afin de permettre
    les tests sur ``http://localhost`` tout en garantissant le transport
    chiffré en pré-production et production.
    """
    return settings.app_env != "development"


def _set_access_cookie(response: Response, token: str) -> None:
    """Dépose l'access token dans un cookie HttpOnly/Secure/SameSite=Lax (Exigence 1.5)."""
    response.set_cookie(
        key=ACCESS_TOKEN_COOKIE_NAME,
        value=token,
        max_age=settings.jwt_access_token_expire_minutes * 60,
        httponly=True,
        secure=_cookies_are_secure(),
        samesite=_COOKIE_SAMESITE,
        path=_COOKIE_PATH,
    )


def _set_refresh_cookie(response: Response, token: str) -> None:
    """Dépose le refresh token dans un cookie HttpOnly/Secure/SameSite=Lax (Exigence 1.5)."""
    response.set_cookie(
        key=REFRESH_TOKEN_COOKIE_NAME,
        value=token,
        max_age=settings.jwt_refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=_cookies_are_secure(),
        samesite=_COOKIE_SAMESITE,
        path=_COOKIE_PATH,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Efface les cookies d'authentification côté web (Exigence 1.7)."""
    response.delete_cookie(ACCESS_TOKEN_COOKIE_NAME, path=_COOKIE_PATH)
    response.delete_cookie(REFRESH_TOKEN_COOKIE_NAME, path=_COOKIE_PATH)


def _decode_jti(token: str) -> str | None:
    """Extrait le claim ``jti`` d'un JWT sans lever d'erreur (usage best-effort).

    Utilisé à la déconnexion pour révoquer la session courante ; en cas de jeton
    illisible, ``None`` est renvoyé et aucune révocation n'est tentée.
    """
    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret_key.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
        )
    except JWTError:
        return None
    jti = payload.get("jti")
    return jti if isinstance(jti, str) else None


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Inscription d'un nouvel Utilisateur",
    # Limitation de débit du point d'accès sensible d'inscription (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("register"))],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Inscription impossible"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def register(
    payload: RegisterRequest,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> UserPublic:
    """Crée un compte ; email déjà connu ⇒ ``400`` générique (Exigences 1.1, 1.2)."""
    try:
        user = await service.register(
            email=payload.email,
            password=payload.password,
            display_name=payload.display_name,
        )
    except AuthError as exc:
        # Message générique : ne révèle jamais si l'email est déjà enregistré (Exigence 1.2).
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return UserPublic.model_validate(user)


@router.post(
    "/login",
    response_model=TokenPair,
    summary="Connexion (émet access + refresh, cookies HttpOnly côté web)",
    # Limitation de débit du point d'accès sensible de connexion (Exigences 26.4, 1.9).
    dependencies=[Depends(default_rate_limit("login"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Identifiants invalides"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def login(
    payload: LoginRequest,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> TokenPair:
    """Vérifie les identifiants et émet un couple de jetons (Exigences 1.4, 1.5, 1.9)."""
    try:
        tokens = await service.authenticate(
            email=payload.email,
            password=payload.password,
        )
    except AuthError as exc:
        # Message générique : ne distingue pas email inconnu et mot de passe erroné (Exigence 1.9).
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Application web : les jetons sont aussi déposés en cookies HttpOnly (Exigence 1.5).
    _set_access_cookie(response, tokens.access_token)
    _set_refresh_cookie(response, tokens.refresh_token)
    return tokens


@router.post(
    "/refresh",
    response_model=AccessToken,
    summary="Émet un nouvel access token à partir d'un refresh token",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Refresh token invalide"}},
)
async def refresh(
    request: Request,
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
) -> AccessToken:
    """Émet un nouvel access token (refresh token du corps ou du cookie) (Exigence 1.6)."""
    refresh_token = await _extract_refresh_token(request)
    if refresh_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_CREDENTIALS_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        access = await service.refresh(refresh_token)
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    # Rafraîchit le cookie d'access token côté web pour prolonger la session.
    _set_access_cookie(response, access.access_token)
    return access


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalide la session courante",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def logout(
    response: Response,
    service: Annotated[AuthService, Depends(get_auth_service)],
    access_token: Annotated[str, Depends(get_access_token)],
) -> Response:
    """Révoque la session courante et efface les cookies (Exigence 1.7)."""
    session_id = _decode_jti(access_token)
    if session_id:
        await service.logout(session_id)
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/me",
    response_model=UserPublic,
    summary="Compte courant sans password_hash",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"}},
)
async def me(
    current_user: Annotated[UserPublic, Depends(get_current_user)],
) -> UserPublic:
    """Retourne les données publiques du compte courant, sans ``password_hash`` (Exigence 1.8)."""
    return current_user


async def _extract_refresh_token(request: Request) -> str | None:
    """Extrait le refresh token du cookie HttpOnly ou du corps JSON de la requête.

    Priorité au cookie HttpOnly ``refresh_token`` (application web, Exigence 1.5) ;
    à défaut, le champ ``refresh_token`` d'un corps JSON (clients API) est utilisé.
    Retourne ``None`` si aucune source exploitable n'est présente.
    """
    cookie_token = request.cookies.get(REFRESH_TOKEN_COOKIE_NAME)
    if cookie_token:
        stripped = cookie_token.strip()
        if stripped:
            return stripped

    # Repli clients API : corps JSON ``{"refresh_token": "..."}``.
    try:
        body: Any = await request.json()
    except Exception:  # corps absent, vide ou non-JSON : aucune source de repli.
        return None
    if isinstance(body, dict):
        value = body.get("refresh_token")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
