"""Tests d'API des Propositions (Exigence 4, tâche 4.5).

Ces tests exercent les points d'accès de ``app/api/v1/proposals.py`` via un
client HTTP (``fastapi.testclient.TestClient``) en **remplaçant** deux
dépendances par des doublures :

* :func:`app.api.v1.proposals.get_proposal_service` → un ``_FakeProposalService``
  qui reproduit le contrat public du vrai Service (create/get/list/update/
  archive, contrôle d'accès auteur/Administrateur) **sans base de données** ;
* :func:`app.api.deps.get_current_user` → un Utilisateur authentifié fixe.

Aucune logique de routage, de sérialisation Pydantic ni de traduction des
exceptions en codes HTTP n'est simulée : c'est bien le vrai code de l'API qui est
exercé. La couche de persistance est la seule remplacée, conformément à la
convention de test du projet.

Couverture :

* ``POST /proposals`` crée un DRAFT (``201``) et exige l'authentification
  (Exigences 4.1, 1.10) ;
* corps invalide ⇒ ``422`` listant les champs en erreur (Exigences 4.2, 31.4) ;
* ``GET /proposals`` renvoie une page filtrée (Exigence 4.3) ;
* ``GET /proposals/{id}`` renvoie le détail ou ``404`` (Exigence 4.4) ;
* ``PUT /proposals/{id}`` met à jour (auteur), ``403`` pour un non-auteur,
  ``404`` si absente (Exigences 4.5, 4.7) ;
* ``DELETE /proposals/{id}`` archive (auteur), ``403`` pour un non-auteur
  (Exigences 4.6, 4.7).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.api.v1 import proposals as proposals_api  # noqa: E402
from app.api.v1.proposals import (  # noqa: E402
    get_duplicate_detection_service,
    get_proposal_service,
    router,
)
from app.schemas.auth import UserPublic  # noqa: E402
from app.schemas.duplicate import SimilarProposal  # noqa: E402
from app.schemas.proposal import (  # noqa: E402
    Page,
    ProposalCreate,
    ProposalDetail,
    ProposalSummary,
    ProposalUpdate,
)
from app.services.proposal_service import (  # noqa: E402
    ProposalNotFoundError,
    ProposalPermissionError,
)

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _ProposalRow:
    """Objet minimal exposant les attributs lus par ``ProposalDetail`` (from_attributes)."""

    def __init__(self, **attrs: Any) -> None:
        defaults: dict[str, Any] = {
            "id": 1,
            "slug": "ma-proposition",
            "title": "Ma proposition",
            "theme_id": 3,
            "author_id": 10,
            "problem": "Un problème",
            "description": "Une description",
            "expected_impact": None,
            "implementation_delay": None,
            "estimated_cost": None,
            "estimated_savings": None,
            "estimated_revenue": None,
            "funding_description": None,
            "legal_constraints": None,
            "status": "DRAFT",
            "version": 1,
            "created_at": _NOW,
            "updated_at": _NOW,
            "versions": [],
        }
        defaults.update(attrs)
        for key, value in defaults.items():
            setattr(self, key, value)


class _FakeProposalService:
    """Doublure du ``ProposalService`` : contrat public sans base de données.

    Elle stocke les Propositions en mémoire et applique le même contrôle d'accès
    auteur/Administrateur que le vrai Service (Exigence 4.7).
    """

    def __init__(self) -> None:
        self.store: dict[int, _ProposalRow] = {}

    async def create(self, author: UserPublic, data: ProposalCreate) -> _ProposalRow:
        row = _ProposalRow(
            id=1,
            author_id=author.id,
            theme_id=data.theme_id,
            title=data.title,
            problem=data.problem,
            description=data.description,
            status="DRAFT",
            version=1,
        )
        self.store[row.id] = row
        return row

    async def get(self, proposal_id: int) -> ProposalDetail:
        row = self.store.get(proposal_id)
        if row is None:
            raise ProposalNotFoundError("Proposition introuvable.")
        return ProposalDetail.model_validate(row)

    async def list(
        self,
        *,
        theme: str | None = None,
        status: str | None = None,
        sort: str = "recent",
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[ProposalSummary]:
        rows = list(self.store.values())
        if status is not None:
            rows = [r for r in rows if r.status == status]
        items = [ProposalSummary.model_validate(r) for r in rows]
        return Page(items=items, total=len(items), page=page, limit=limit)

    async def update(
        self, actor: UserPublic, proposal_id: int, data: ProposalUpdate
    ) -> _ProposalRow:
        row = self.store.get(proposal_id)
        if row is None:
            raise ProposalNotFoundError("Proposition introuvable.")
        self._authorize(actor, row)
        changes = data.model_dump(exclude_unset=True, exclude={"change_summary"})
        for field, value in changes.items():
            setattr(row, field, value)
        row.version += 1
        return row

    async def archive(self, actor: UserPublic, proposal_id: int) -> _ProposalRow:
        row = self.store.get(proposal_id)
        if row is None:
            raise ProposalNotFoundError("Proposition introuvable.")
        self._authorize(actor, row)
        row.status = "ARCHIVED"
        return row

    @staticmethod
    def _authorize(actor: UserPublic, row: _ProposalRow) -> None:
        if actor.is_admin or actor.id == row.author_id:
            return
        raise ProposalPermissionError("Action réservée à l'auteur ou à un administrateur.")


class _FakeDuplicateDetectionService:
    """Doublure du ``DuplicateDetectionService`` : renvoie des similaires fixes.

    Aucun appel à un fournisseur d'embeddings réel : la liste retournée est
    contrôlée par le test (vide par défaut, donc aucun doublon).
    """

    def __init__(self, similar: list[SimilarProposal] | None = None) -> None:
        self.similar = similar or []
        self.calls: list[tuple[str, str]] = []

    async def find_similar(
        self,
        title: str,
        description: str,
        *,
        threshold: float = 0.82,
        top_k: int = 5,
        exclude_id: int | None = None,
    ) -> list[SimilarProposal]:
        self.calls.append((title, description))
        return list(self.similar)


def _user(user_id: int = 10, *, is_admin: bool = False) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"user{user_id}@example.org",
        display_name=f"User {user_id}",
        is_active=True,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_client(
    service: _FakeProposalService,
    *,
    current_user: UserPublic | None,
    duplicate_service: _FakeDuplicateDetectionService | None = None,
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/proposals")
    app.dependency_overrides[get_proposal_service] = lambda: service
    app.dependency_overrides[get_duplicate_detection_service] = (
        lambda: duplicate_service or _FakeDuplicateDetectionService()
    )
    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app, raise_server_exceptions=True)


_VALID_BODY = {
    "theme_id": 3,
    "title": "Ma proposition",
    "problem": "Un problème réel",
    "description": "Une description détaillée",
}


def test_create_proposal_returns_201_draft() -> None:
    """POST /proposals crée un DRAFT et renvoie 201 (Exigences 4.1, 1.10)."""
    service = _FakeProposalService()
    client = _build_client(service, current_user=_user())

    resp = client.post("/api/v1/proposals", json=_VALID_BODY)

    assert resp.status_code == 201
    body = resp.json()
    proposal = body["proposal"]
    assert proposal["status"] == "DRAFT"
    assert proposal["version"] == 1
    assert proposal["author_id"] == 10
    assert proposal["theme_id"] == 3
    # Aucun doublon détecté par défaut : pas de message ni d'actions (Exigence 5.2).
    assert body["similar"] == []
    assert body["message"] is None
    assert body["actions"] is None


def test_create_proposal_reports_similar_with_actions() -> None:
    """POST /proposals expose les doublons et les 3 actions (Exigences 5.1, 5.2, 5.3)."""
    service = _FakeProposalService()
    duplicate = _FakeDuplicateDetectionService(
        similar=[
            SimilarProposal(id=42, slug="mesure-proche", title="Mesure proche", similarity=0.95)
        ]
    )
    client = _build_client(service, current_user=_user(), duplicate_service=duplicate)

    resp = client.post("/api/v1/proposals", json=_VALID_BODY)

    # La création n'est jamais bloquée par la présence de doublons (Exigence 5.3).
    assert resp.status_code == 201
    body = resp.json()
    assert body["proposal"]["status"] == "DRAFT"
    assert body["message"] == "Des propositions similaires existent"
    assert len(body["similar"]) == 1
    assert body["similar"][0]["id"] == 42
    assert body["similar"][0]["slug"] == "mesure-proche"
    # Les trois actions Consulter / Créer quand même / Améliorer sont proposées.
    actions = body["actions"]
    assert actions["view"] == "Consulter"
    assert actions["create_anyway"] == "Créer quand même"
    assert actions["improve"] == "Améliorer une proposition existante"


def test_create_proposal_requires_authentication() -> None:
    """Sans authentification, POST /proposals renvoie 401 (Exigence 1.10)."""
    service = _FakeProposalService()
    client = _build_client(service, current_user=None)

    resp = client.post("/api/v1/proposals", json=_VALID_BODY)

    assert resp.status_code == 401


def test_create_proposal_invalid_body_returns_422_with_fields() -> None:
    """Corps invalide ⇒ 422 listant les champs en erreur (Exigences 4.2, 31.4)."""
    service = _FakeProposalService()
    client = _build_client(service, current_user=_user())

    # ``theme_id`` manquant + ``title`` trop court.
    resp = client.post(
        "/api/v1/proposals",
        json={"title": "x", "problem": "p", "description": "d"},
    )

    assert resp.status_code == 422
    fields = {tuple(err["loc"][-1:]) for err in resp.json()["detail"]}
    assert ("theme_id",) in fields
    assert ("title",) in fields


def test_list_proposals_is_public_and_filters_status() -> None:
    """GET /proposals renvoie une page filtrée sans authentification (Exigence 4.3)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, status="DRAFT")
    service.store[2] = _ProposalRow(id=2, status="PUBLISHED")
    client = _build_client(service, current_user=None)

    resp = client.get("/api/v1/proposals", params={"status": "PUBLISHED", "limit": 5})

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["limit"] == 5
    assert body["items"][0]["status"] == "PUBLISHED"


def test_get_proposal_detail_and_404() -> None:
    """GET /proposals/{id} renvoie le détail, 404 si absent (Exigence 4.4)."""
    service = _FakeProposalService()
    service.store[7] = _ProposalRow(id=7, title="Détail")
    client = _build_client(service, current_user=None)

    ok = client.get("/api/v1/proposals/7")
    assert ok.status_code == 200
    assert ok.json()["title"] == "Détail"

    missing = client.get("/api/v1/proposals/999")
    assert missing.status_code == 404


def test_update_proposal_by_author_increments_version() -> None:
    """PUT /proposals/{id} par l'auteur applique la modification (Exigences 4.5, 4.7)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, author_id=10, version=1)
    client = _build_client(service, current_user=_user(10))

    resp = client.put(
        "/api/v1/proposals/1",
        json={"title": "Titre modifié", "change_summary": "MàJ"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Titre modifié"
    assert body["version"] == 2


def test_update_proposal_by_non_author_returns_403() -> None:
    """PUT /proposals/{id} par un non-auteur non-admin ⇒ 403 (Exigence 4.7)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, author_id=10)
    client = _build_client(service, current_user=_user(99))

    resp = client.put("/api/v1/proposals/1", json={"title": "Pirate"})

    assert resp.status_code == 403


def test_update_missing_proposal_returns_404() -> None:
    """PUT sur une Proposition absente ⇒ 404 (Exigence 4.4)."""
    service = _FakeProposalService()
    client = _build_client(service, current_user=_user(10))

    resp = client.put("/api/v1/proposals/404", json={"title": "Titre neuf"})

    assert resp.status_code == 404


def test_delete_proposal_by_author_archives() -> None:
    """DELETE /proposals/{id} par l'auteur archive la Proposition (Exigence 4.6)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, author_id=10, status="DRAFT")
    client = _build_client(service, current_user=_user(10))

    resp = client.delete("/api/v1/proposals/1")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ARCHIVED"


def test_delete_proposal_by_admin_archives() -> None:
    """Un Administrateur peut archiver la Proposition d'autrui (Exigence 4.7)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, author_id=10, status="DRAFT")
    client = _build_client(service, current_user=_user(1, is_admin=True))

    resp = client.delete("/api/v1/proposals/1")

    assert resp.status_code == 200
    assert resp.json()["status"] == "ARCHIVED"


def test_delete_proposal_by_non_author_returns_403() -> None:
    """DELETE par un non-auteur non-admin ⇒ 403 (Exigence 4.7)."""
    service = _FakeProposalService()
    service.store[1] = _ProposalRow(id=1, author_id=10)
    client = _build_client(service, current_user=_user(99))

    resp = client.delete("/api/v1/proposals/1")

    assert resp.status_code == 403
