"""Tests d'API d'authentification (Exigence 1, tâche 3.5).

Ces tests exercent la couche HTTP de ``app/api/v1/auth.py`` (câblage des points
d'accès, codes de statut, sérialisation et **généricité** des messages d'erreur)
sans base de données ni Redis : l'``AuthService`` est remplacé par une
implémentation factice qui reproduit son contrat en mémoire. La logique réelle du
Service (hachage, JWT, révocation) est couverte par ``tests/unit/test_auth_service.py``.
On valide ici uniquement le comportement de l'API :

* ``POST /auth/register`` — ``201`` à la création (Exigence 1.1) ; email déjà
  connu ⇒ ``400`` à message **générique** ne révélant pas l'existence du compte
  (Exigence 1.2) ; corps invalide ⇒ ``422`` (validation Pydantic) ;
* ``POST /auth/login`` — ``200`` + couple access/refresh (Exigence 1.4) ;
  identifiants invalides ⇒ ``401`` à message **générique** (Exigence 1.9), le
  message étant identique que l'email soit inconnu ou le mot de passe erroné.

Le montage suit celui de production : le routeur ``auth.router`` est inclus sous
le préfixe ``/auth`` (comme dans ``app.api.v1.router``). Les dépendances de
limitation de débit sont neutralisées pour garder les tests hermétiques.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import auth
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import AccessToken, TokenPair, UserPublic
from app.services.auth_service import (
    GENERIC_CREDENTIALS_ERROR,
    GENERIC_REGISTRATION_ERROR,
    AuthError,
)

pytestmark = pytest.mark.api


def _user(user_id: int = 1, email: str = "citoyen@example.org") -> UserPublic:
    """Construit un ``UserPublic`` minimal (jamais de ``password_hash``, Exigence 1.8)."""
    now = datetime.now(UTC)
    return UserPublic(
        id=user_id,
        email=email,
        display_name="Citoyen",
        is_active=True,
        is_verified=False,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )


class _FakeUser:
    """Objet minimal validable par ``UserPublic.model_validate`` (from_attributes)."""

    def __init__(self, user_id: int, email: str, display_name: str) -> None:
        now = datetime.now(UTC)
        self.id = user_id
        self.email = email
        self.display_name = display_name
        self.is_active = True
        self.is_verified = False
        self.is_admin = False
        self.created_at = now
        self.updated_at = now


class _FakeAuthService:
    """``AuthService`` factice reproduisant le contrat d'inscription/connexion.

    Les comptes connus sont indexés par email normalisé (minuscules). Les messages
    d'erreur restent **génériques** afin de refléter le contrat du Service réel
    (Exigences 1.2, 1.9).
    """

    def __init__(self, *, known_emails: dict[str, str] | None = None) -> None:
        # email normalisé -> mot de passe valide attendu.
        self._known = {e.lower(): p for e, p in (known_emails or {}).items()}
        self._next_id = 1

    async def register(self, email: str, password: str, display_name: str) -> _FakeUser:
        normalized = email.strip().lower()
        if normalized in self._known:
            # Ne révèle pas que l'email est déjà enregistré (Exigence 1.2).
            raise AuthError(GENERIC_REGISTRATION_ERROR)
        self._known[normalized] = password
        user = _FakeUser(self._next_id, normalized, display_name)
        self._next_id += 1
        return user

    async def authenticate(self, email: str, password: str) -> TokenPair:
        normalized = email.strip().lower()
        expected = self._known.get(normalized)
        # Message identique que l'email soit inconnu ou le mot de passe erroné (Exigence 1.9).
        if expected is None or expected != password:
            raise AuthError(GENERIC_CREDENTIALS_ERROR)
        return TokenPair(
            access_token="access-token-value",
            refresh_token="refresh-token-value",
            expires_in=900,
        )

    async def refresh(self, refresh_token: str) -> AccessToken:  # pragma: no cover - non ciblé par 3.5
        raise AuthError(GENERIC_CREDENTIALS_ERROR)

    async def logout(self, session_id: str) -> None:  # pragma: no cover - non ciblé par 3.5
        return None


def _build_app(service: _FakeAuthService) -> FastAPI:
    """Assemble une application ne montant que le routeur d'auth sous ``/auth``.

    Le service factice est injecté via la surcharge de ``get_auth_service`` ; les
    dépendances de limitation de débit (inscription, connexion) sont neutralisées
    pour éviter tout accès à Redis pendant les tests.
    """
    app = FastAPI()
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.dependency_overrides[auth.get_auth_service] = lambda: service
    # Neutralise les gardes de débit déclarées sur /register et /login.
    app.dependency_overrides[default_rate_limit("register")] = lambda: None
    app.dependency_overrides[default_rate_limit("login")] = lambda: None
    return app


def _client(service: _FakeAuthService) -> TestClient:
    return TestClient(_build_app(service))


# --------------------------------------------------------------------------- #
# POST /auth/register                                                          #
# --------------------------------------------------------------------------- #
def test_register_returns_201_and_public_user() -> None:
    """L'inscription d'un nouvel email renvoie ``201`` et le compte public (Exigence 1.1)."""
    client = _client(_FakeAuthService())

    response = client.post(
        "/auth/register",
        json={
            "email": "nouveau@example.org",
            "password": "motdepasse123",
            "display_name": "Nouveau Citoyen",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "nouveau@example.org"
    assert body["display_name"] == "Nouveau Citoyen"
    assert body["is_admin"] is False
    # Le mot de passe / hachage ne doit jamais transiter (Exigences 1.3, 1.8).
    assert "password" not in body
    assert "password_hash" not in body


def test_register_known_email_returns_400_generic_message() -> None:
    """Un email déjà connu est refusé par un ``400`` à message générique (Exigence 1.2)."""
    client = _client(_FakeAuthService(known_emails={"connu@example.org": "secret123"}))

    response = client.post(
        "/auth/register",
        json={
            "email": "connu@example.org",
            "password": "unautremdp1",
            "display_name": "Doublon",
        },
    )

    assert response.status_code == 400
    detail = response.json()["detail"]
    # Le message est générique et ne révèle pas que l'email est déjà enregistré (Exigence 1.2).
    assert detail == GENERIC_REGISTRATION_ERROR
    assert "connu@example.org" not in detail


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "pas-un-email", "password": "motdepasse123", "display_name": "X"},
        {"email": "ok@example.org", "password": "court", "display_name": "X"},
        {"email": "ok@example.org", "password": "motdepasse123", "display_name": ""},
        {"password": "motdepasse123", "display_name": "X"},
    ],
)
def test_register_invalid_body_returns_422(payload: dict[str, str]) -> None:
    """Un corps invalide est rejeté par la validation Pydantic (``422``)."""
    client = _client(_FakeAuthService())

    response = client.post("/auth/register", json=payload)

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# POST /auth/login                                                             #
# --------------------------------------------------------------------------- #
def test_login_returns_200_and_token_pair() -> None:
    """Des identifiants valides renvoient ``200`` et un couple access/refresh (Exigence 1.4)."""
    client = _client(_FakeAuthService(known_emails={"citoyen@example.org": "secret123"}))

    response = client.post(
        "/auth/login",
        json={"email": "citoyen@example.org", "password": "secret123"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"
    assert body["expires_in"] > 0


def test_login_sets_httponly_auth_cookies() -> None:
    """La connexion web dépose les jetons dans des cookies HttpOnly (Exigence 1.5)."""
    client = _client(_FakeAuthService(known_emails={"citoyen@example.org": "secret123"}))

    response = client.post(
        "/auth/login",
        json={"email": "citoyen@example.org", "password": "secret123"},
    )

    assert response.status_code == 200
    # TestClient regroupe les en-têtes ; on vérifie la présence des deux cookies.
    raw = response.headers.get("set-cookie", "")
    assert "access_token=" in raw or "access_token" in response.cookies
    assert "refresh_token=" in raw or "refresh_token" in response.cookies


def test_login_invalid_credentials_returns_401_generic() -> None:
    """Un mot de passe erroné est refusé par un ``401`` générique (Exigence 1.9)."""
    client = _client(_FakeAuthService(known_emails={"citoyen@example.org": "secret123"}))

    response = client.post(
        "/auth/login",
        json={"email": "citoyen@example.org", "password": "mauvais-mdp"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == GENERIC_CREDENTIALS_ERROR


def test_login_unknown_email_returns_same_generic_message() -> None:
    """Un email inconnu produit le **même** ``401`` générique qu'un mot de passe erroné (Exigence 1.9)."""
    client = _client(_FakeAuthService(known_emails={"citoyen@example.org": "secret123"}))

    unknown = client.post(
        "/auth/login",
        json={"email": "inconnu@example.org", "password": "peu-importe"},
    )
    wrong_password = client.post(
        "/auth/login",
        json={"email": "citoyen@example.org", "password": "mauvais-mdp"},
    )

    assert unknown.status_code == wrong_password.status_code == 401
    # Aucune distinction entre email inconnu et mot de passe erroné (Exigence 1.9).
    assert unknown.json()["detail"] == wrong_password.json()["detail"]
    assert unknown.json()["detail"] == GENERIC_CREDENTIALS_ERROR
