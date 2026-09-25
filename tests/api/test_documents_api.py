"""Tests d'API d'ingestion documentaire (Exigences 11.1, 23.2, 23.3 ; tâche 6.7).

Ces tests exercent la couche HTTP de ``app/api/v1/documents.py`` (câblage du
point d'accès, code ``202``, garde d'administration, sérialisation) **sans**
Celery ni broker Redis : l'émetteur de tâche (:func:`documents.get_task_dispatcher`)
est remplacé par une implémentation factice qui enregistre l'appel au lieu de
placer un message sur le broker. On valide ici uniquement le comportement de
l'API :

* ``POST /admin/documents/ingest`` — ``202`` (Administrateur), l'émetteur reçoit
  bien ``(url, source_id)`` (Exigences 11.1, 23.2) ;
* l'API n'exécute pas le pipeline : elle se contente d'émettre la tâche
  (Exigence 23.3) — vérifié via l'émetteur factice ;
* ``401`` si anonyme, ``403`` si Utilisateur non-administrateur ;
* ``422`` si le corps est invalide (url vide, source_id ≤ 0).

Le montage suit celui de production : le routeur ``documents.router`` est inclus
sous le préfixe ``/admin`` (comme dans ``app.api.v1.router``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import documents
from app.schemas.auth import UserPublic

pytestmark = pytest.mark.api


class _FakeDispatcher:
    """Émetteur de tâche factice : enregistre les appels au lieu d'émettre sur Redis."""

    def __init__(self, *, task_id: str | None = "task-123") -> None:
        self.calls: list[tuple[str, int]] = []
        self._task_id = task_id

    def __call__(self, url: str, source_id: int) -> str | None:
        self.calls.append((url, source_id))
        return self._task_id


def _user(*, is_admin: bool, user_id: int = 1) -> UserPublic:
    now = datetime.now(UTC)
    return UserPublic(
        id=user_id,
        email="admin@example.org",
        display_name="Admin",
        is_active=True,
        is_verified=True,
        is_admin=is_admin,
        created_at=now,
        updated_at=now,
    )


def _build_app(dispatcher: _FakeDispatcher, *, role: str | None) -> FastAPI:
    """Assemble une application ne montant que le routeur d'ingestion.

    ``role`` vaut ``"admin"``, ``"user"`` (authentifié non-admin) ou ``None``
    (anonyme). Sans surcharge d'auth, la garde :func:`require_admin` refuse
    (``401``).
    """
    app = FastAPI()
    app.include_router(documents.router, prefix="/admin", tags=["documents"])
    app.dependency_overrides[documents.get_task_dispatcher] = lambda: dispatcher
    if role == "admin":
        app.dependency_overrides[get_current_user] = lambda: _user(is_admin=True)
    elif role == "user":
        app.dependency_overrides[get_current_user] = lambda: _user(is_admin=False)
    return app


def _client(dispatcher: _FakeDispatcher, *, role: str | None) -> TestClient:
    return TestClient(_build_app(dispatcher, role=role))


# --------------------------------------------------------------------------- #
# Cas nominal — Administrateur                                                 #
# --------------------------------------------------------------------------- #
def test_ingest_returns_202_and_dispatches_task_for_admin() -> None:
    """L'Administrateur déclenche l'ingestion : ``202`` + tâche émise (Exigences 11.1, 23.2)."""
    dispatcher = _FakeDispatcher(task_id="celery-abc")
    client = _client(dispatcher, role="admin")

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "https://exemple.fr/rapport.pdf", "source_id": 3},
    )

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "accepted"
    assert body["task_id"] == "celery-abc"
    assert body["url"] == "https://exemple.fr/rapport.pdf"
    assert body["source_id"] == 3
    # L'API a émis la tâche exactement une fois, avec (url, source_id) (Exigence 23.3).
    assert dispatcher.calls == [("https://exemple.fr/rapport.pdf", 3)]


def test_ingest_does_not_block_when_dispatcher_returns_no_id() -> None:
    """Le back-end peut ne pas fournir d'id : la réponse reste ``202`` (Exigence 23.3)."""
    dispatcher = _FakeDispatcher(task_id=None)
    client = _client(dispatcher, role="admin")

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "https://exemple.fr/a.html", "source_id": 1},
    )

    assert response.status_code == 202
    assert response.json()["task_id"] is None
    assert dispatcher.calls == [("https://exemple.fr/a.html", 1)]


# --------------------------------------------------------------------------- #
# Contrôle d'accès                                                             #
# --------------------------------------------------------------------------- #
def test_ingest_requires_authentication() -> None:
    """Le déclenchement anonyme est refusé par un ``401``."""
    dispatcher = _FakeDispatcher()
    client = _client(dispatcher, role=None)

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "https://exemple.fr/a.pdf", "source_id": 1},
    )

    assert response.status_code == 401
    assert dispatcher.calls == []


def test_ingest_forbidden_for_non_admin() -> None:
    """Le déclenchement par un Utilisateur non-administrateur est refusé par un ``403``."""
    dispatcher = _FakeDispatcher()
    client = _client(dispatcher, role="user")

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "https://exemple.fr/a.pdf", "source_id": 1},
    )

    assert response.status_code == 403
    assert dispatcher.calls == []


# --------------------------------------------------------------------------- #
# Validation du corps                                                          #
# --------------------------------------------------------------------------- #
def test_ingest_rejects_empty_url() -> None:
    """Une URL vide entraîne une erreur de validation ``422``."""
    dispatcher = _FakeDispatcher()
    client = _client(dispatcher, role="admin")

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "", "source_id": 1},
    )

    assert response.status_code == 422
    assert dispatcher.calls == []


def test_ingest_rejects_non_positive_source_id() -> None:
    """Un ``source_id`` non strictement positif entraîne un ``422``."""
    dispatcher = _FakeDispatcher()
    client = _client(dispatcher, role="admin")

    response = client.post(
        "/admin/documents/ingest",
        json={"url": "https://exemple.fr/a.pdf", "source_id": 0},
    )

    assert response.status_code == 422
    assert dispatcher.calls == []
