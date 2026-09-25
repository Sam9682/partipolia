"""Tests d'API et Web des fonctions RGPD (Exigences 28.3, 28.4, 28.5 ; tâche 10.3).

Deux volets sans base de données :

* **API ``/api/v1/privacy``** — export des données, gestion du consentement et
  suppression de compte, réservés à l'Utilisateur authentifié (``401`` si
  anonyme, Exigence 31.3) ; politique de rétention publique (Exigence 28.5). Le
  :class:`~app.services.privacy_service.PrivacyService` est remplacé par un
  service factice reproduisant son contrat en mémoire ; la vraie logique est
  couverte par ``tests/unit/test_privacy_service.py``.
* **Pages Web RGPD** — ``/mentions-legales``, ``/confidentialite``, ``/cookies``,
  ``/conditions`` rendues sans authentification (Exigence 28.3), la page
  confidentialité exposant la politique de rétention et l'absence de profilage
  (Exigences 28.5, 28.1, 28.2).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import privacy
from app.schemas.auth import UserPublic
from app.schemas.privacy import (
    AccountDeletionResult,
    ConsentState,
    DataExport,
    ExportedAccount,
    RetentionPolicy,
)
from app.services.privacy_service import PrivacyService
from app.web import router as web

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _user(user_id: int = 1) -> UserPublic:
    return UserPublic(
        id=user_id,
        email="citoyen@example.org",
        display_name="Citoyen",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


class _FakePrivacyService:
    """``PrivacyService`` factice reproduisant le contrat RGPD en mémoire."""

    def __init__(self) -> None:
        self._consents: dict[str, bool] = {"analytics": False}
        self.deleted_user_id: int | None = None

    async def export_user_data(self, user_id: int) -> DataExport:
        account = ExportedAccount(
            id=user_id,
            email="citoyen@example.org",
            display_name="Citoyen",
            is_active=True,
            is_verified=True,
            is_admin=False,
            consents=self._consents,
            created_at=_NOW,
            updated_at=_NOW,
        )
        return DataExport(generated_at=_NOW, account=account)

    async def get_consents(self, user_id: int) -> ConsentState:
        return ConsentState(consents=self._consents)

    async def update_consents(
        self, user_id: int, consents: dict[str, bool]
    ) -> ConsentState:
        self._consents = {str(k): bool(v) for k, v in consents.items()}
        return ConsentState(consents=self._consents)

    async def delete_account(self, user_id: int) -> AccountDeletionResult:
        self.deleted_user_id = user_id
        return AccountDeletionResult(
            deleted=True,
            anonymized_proposals=1,
            deleted_votes=2,
            deleted_arguments=0,
            deleted_comments=1,
        )


def _build_api(service: _FakePrivacyService, *, authenticated: bool) -> FastAPI:
    app = FastAPI()
    app.include_router(privacy.router, prefix="/privacy", tags=["privacy"])
    app.dependency_overrides[privacy.get_privacy_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_user] = _user
    return app


def _api_client(service: _FakePrivacyService, *, authenticated: bool) -> TestClient:
    return TestClient(_build_api(service, authenticated=authenticated))


# --------------------------------------------------------------------------- #
# Politique de rétention — publique (Exigence 28.5)                            #
# --------------------------------------------------------------------------- #
def test_retention_is_public_and_complete() -> None:
    """``GET /privacy/retention`` est public et couvre toutes les catégories (Exigence 28.5)."""
    response = _api_client(_FakePrivacyService(), authenticated=False).get(
        "/privacy/retention"
    )

    assert response.status_code == 200
    policy = RetentionPolicy.model_validate(response.json())
    categories = {rule.category for rule in policy.rules}
    assert "Comptes" in categories
    assert "Votes" in categories
    assert "Signalements" in categories


# --------------------------------------------------------------------------- #
# Export des données (Exigence 28.4)                                           #
# --------------------------------------------------------------------------- #
def test_export_requires_authentication() -> None:
    """``GET /privacy/export`` anonyme est refusé (``401``) (Exigence 31.3)."""
    response = _api_client(_FakePrivacyService(), authenticated=False).get(
        "/privacy/export"
    )
    assert response.status_code == 401


def test_export_returns_account_without_password() -> None:
    """L'export renvoie le compte sans jamais exposer le mot de passe (Exigences 28.4, 1.3)."""
    response = _api_client(_FakePrivacyService(), authenticated=True).get(
        "/privacy/export"
    )

    assert response.status_code == 200
    assert response.json()["account"]["email"] == "citoyen@example.org"
    assert "password" not in response.text


# --------------------------------------------------------------------------- #
# Gestion du consentement (Exigence 28.4)                                      #
# --------------------------------------------------------------------------- #
def test_get_consents_requires_authentication() -> None:
    """``GET /privacy/consents`` anonyme est refusé (``401``)."""
    response = _api_client(_FakePrivacyService(), authenticated=False).get(
        "/privacy/consents"
    )
    assert response.status_code == 401


def test_update_consents_replaces_state() -> None:
    """``PUT /privacy/consents`` remplace le registre de consentement (Exigence 28.4)."""
    service = _FakePrivacyService()
    client = _api_client(service, authenticated=True)

    response = client.put(
        "/privacy/consents", json={"consents": {"newsletter": True}}
    )

    assert response.status_code == 200
    assert response.json()["consents"] == {"newsletter": True}
    # La lecture ultérieure reflète la mise à jour.
    assert client.get("/privacy/consents").json()["consents"] == {"newsletter": True}


# --------------------------------------------------------------------------- #
# Suppression de compte (Exigence 28.4)                                        #
# --------------------------------------------------------------------------- #
def test_delete_account_requires_authentication() -> None:
    """``DELETE /privacy/account`` anonyme est refusé (``401``) (Exigence 31.3)."""
    response = _api_client(_FakePrivacyService(), authenticated=False).delete(
        "/privacy/account"
    )
    assert response.status_code == 401


def test_delete_account_succeeds_and_clears_cookies() -> None:
    """La suppression renvoie le récapitulatif et efface les cookies d'auth (Exigence 28.4)."""
    service = _FakePrivacyService()
    client = _api_client(service, authenticated=True)

    response = client.delete("/privacy/account")

    assert response.status_code == 200
    body = response.json()
    assert body["deleted"] is True
    assert body["anonymized_proposals"] == 1
    assert service.deleted_user_id == 1
    # Les cookies d'authentification sont invalidés (Set-Cookie avec expiration).
    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie


# --------------------------------------------------------------------------- #
# Pages Web RGPD publiques (Exigence 28.3)                                     #
# --------------------------------------------------------------------------- #
def _web_client() -> TestClient:
    app = FastAPI()
    app.include_router(web.web_router)
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    ["/mentions-legales", "/confidentialite", "/cookies", "/conditions"],
)
def test_rgpd_pages_are_public_html(path: str) -> None:
    """Les quatre pages RGPD répondent ``200`` en HTML, sans authentification (Exigence 28.3)."""
    response = _web_client().get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


def test_confidentialite_shows_retention_and_no_profiling() -> None:
    """La page confidentialité expose la rétention et l'absence de profilage (Exigences 28.5, 28.2)."""
    body = _web_client().get("/confidentialite").text

    # Politique de rétention explicite (catégories présentes).
    assert "Conversations IA" in body
    assert "Signalements" in body
    # Absence de profil politique individuel affirmée (Exigences 28.1, 28.2).
    assert "profil politique individuel" in body
    # Fonctions RGPD décrites (Exigence 28.4).
    assert "Export de vos données" in body
    assert "Suppression de compte" in body
