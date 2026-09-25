"""Tests d'API des Sources documentaires (Exigence 10 ; tâche 6.1).

Ces tests exercent les points d'accès de ``app/api/v1/sources.py`` via un client
HTTP (``fastapi.testclient.TestClient``) en **remplaçant** deux dépendances par
des doublures :

* la fabrique de Service (``get_source_service``) → une doublure reproduisant le
  contrat public **sans base de données** ;
* :func:`app.api.deps.require_admin` → un Administrateur fixe (ou omis pour
  vérifier le refus ``401`` sur les actions d'administration).

Seule la couche de persistance est remplacée : le vrai code de routage, de
sérialisation Pydantic et de traduction des exceptions en codes HTTP est exercé.

Couverture :

* ``GET /sources`` — liste publique, filtrable par ``source_type``
  (Exigences 10.1, 10.2) ;
* ``GET /sources?source_type=…`` invalide ⇒ ``422`` (validation de l'énumération) ;
* ``POST /sources`` — création réservée à l'Administrateur (``401`` sans admin ;
  ``201`` avec admin) (Exigences 10.1, 10.2, 10.4) ;
* ``POST /proposals/{id}/sources`` — rattachement avec ``relevance_score``
  (``201``), refus ``401`` sans admin, ``404`` si Proposition/Source absente,
  ``422`` si ``relevance_score`` hors ``[0, 1]`` (Exigences 10.3, 10.4).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import require_admin  # noqa: E402
from app.api.v1.sources import get_source_service  # noqa: E402
from app.api.v1.sources import proposal_sources_router  # noqa: E402
from app.api.v1.sources import router as sources_router  # noqa: E402
from app.models.source import SOURCE_TYPES  # noqa: E402
from app.schemas.auth import UserPublic  # noqa: E402
from app.services.source_service import (  # noqa: E402
    InvalidSourceTypeError,
    ProposalNotFoundError,
    SourceNotFoundError,
)

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _Row:
    """Objet minimal exposant les attributs lus par les schémas (from_attributes)."""

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


class _FakeSourceService:
    """Doublure du :class:`SourceService` reproduisant le contrat public sans BD."""

    def __init__(self) -> None:
        self.sources: dict[int, _Row] = {}
        self.proposals: set[int] = set()
        self._next_source = 1
        self._next_link = 1

    async def create(self, data: Any) -> _Row:
        source_type = data.source_type.value
        if source_type not in SOURCE_TYPES:
            raise InvalidSourceTypeError(source_type)
        source = _Row(
            id=self._next_source,
            title=data.title,
            url=data.url,
            publisher=data.publisher,
            source_type=source_type,
            publication_date=data.publication_date,
            is_verified=data.is_verified,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.sources[source.id] = source
        self._next_source += 1
        return source

    async def attach_to_proposal(self, proposal_id: int, data: Any) -> _Row:
        if proposal_id not in self.proposals:
            raise ProposalNotFoundError(str(proposal_id))
        if data.source_id not in self.sources:
            raise SourceNotFoundError(str(data.source_id))
        link = _Row(
            id=self._next_link,
            proposal_id=proposal_id,
            source_id=data.source_id,
            relevance_score=data.relevance_score,
            note=data.note,
            created_at=_NOW,
        )
        self._next_link += 1
        return link

    async def list_sources(self, *, source_type: str | None = None) -> list[_Row]:
        if source_type is not None and source_type not in SOURCE_TYPES:
            raise InvalidSourceTypeError(source_type)
        values = list(self.sources.values())
        if source_type is not None:
            values = [s for s in values if s.source_type == source_type]
        return sorted(values, key=lambda s: s.id, reverse=True)


def _admin(user_id: int = 1) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"admin{user_id}@example.org",
        display_name=f"Admin {user_id}",
        is_active=True,
        is_verified=True,
        is_admin=True,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_client(
    service: _FakeSourceService, *, admin: UserPublic | None
) -> TestClient:
    app = FastAPI()
    app.include_router(sources_router, prefix="/api/v1/sources")
    app.include_router(proposal_sources_router, prefix="/api/v1/proposals")
    app.dependency_overrides[get_source_service] = lambda: service
    if admin is not None:
        app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app, raise_server_exceptions=True)


# --------------------------------------------------------------------------- #
# GET /sources (Exigences 10.1, 10.2)                                           #
# --------------------------------------------------------------------------- #
def test_list_sources_is_public() -> None:
    """GET /sources est public et renvoie les Sources (Exigence 10.1)."""
    service = _FakeSourceService()
    service.sources[1] = _Row(
        id=1,
        title="Rapport officiel",
        url="https://example.org/rapport",
        publisher="État",
        source_type="OFFICIAL",
        publication_date=date(2023, 5, 1),
        is_verified=True,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_client(service, admin=None)

    resp = client.get("/api/v1/sources")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["title"] == "Rapport officiel"
    assert body[0]["source_type"] == "OFFICIAL"


def test_list_sources_filters_by_source_type() -> None:
    """GET /sources?source_type=… filtre sur le type (Exigence 10.2)."""
    service = _FakeSourceService()
    service.sources[1] = _Row(
        id=1,
        title="A",
        url=None,
        publisher=None,
        source_type="OFFICIAL",
        publication_date=None,
        is_verified=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    service.sources[2] = _Row(
        id=2,
        title="B",
        url=None,
        publisher=None,
        source_type="MEDIA",
        publication_date=None,
        is_verified=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_client(service, admin=None)

    resp = client.get("/api/v1/sources", params={"source_type": "MEDIA"})

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["title"] == "B"


def test_list_sources_rejects_invalid_source_type_with_422() -> None:
    """Un ``source_type`` hors énumération ⇒ 422 (validation Pydantic, Exigence 10.2)."""
    service = _FakeSourceService()
    client = _build_client(service, admin=None)

    resp = client.get("/api/v1/sources", params={"source_type": "UNKNOWN"})

    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /sources (Exigences 10.1, 10.2, 10.4)                                    #
# --------------------------------------------------------------------------- #
def test_create_source_requires_admin() -> None:
    """Sans Administrateur, POST /sources renvoie 401 (Exigence 10.4)."""
    service = _FakeSourceService()
    client = _build_client(service, admin=None)

    resp = client.post(
        "/api/v1/sources",
        json={"title": "Nouvelle source", "source_type": "REPORT"},
    )

    assert resp.status_code == 401


def test_create_source_as_admin_returns_201() -> None:
    """POST /sources par un Administrateur crée la Source (201) (Exigences 10.1, 10.4)."""
    service = _FakeSourceService()
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/sources",
        json={
            "title": "Étude académique",
            "url": "https://example.org/etude",
            "publisher": "Université",
            "source_type": "ACADEMIC",
            "is_verified": True,
        },
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Étude académique"
    assert body["source_type"] == "ACADEMIC"
    assert body["is_verified"] is True


def test_create_source_rejects_invalid_source_type_with_422() -> None:
    """Un ``source_type`` hors énumération ⇒ 422 (validation Pydantic, Exigence 10.2)."""
    service = _FakeSourceService()
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/sources",
        json={"title": "Source", "source_type": "INVALID"},
    )

    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /proposals/{id}/sources (Exigences 10.3, 10.4)                           #
# --------------------------------------------------------------------------- #
def test_attach_source_requires_admin() -> None:
    """Sans Administrateur, POST /proposals/{id}/sources renvoie 401 (Exigence 10.4)."""
    service = _FakeSourceService()
    client = _build_client(service, admin=None)

    resp = client.post(
        "/api/v1/proposals/1/sources",
        json={"source_id": 1, "relevance_score": 0.5},
    )

    assert resp.status_code == 401


def test_attach_source_as_admin_returns_201() -> None:
    """POST /proposals/{id}/sources rattache avec ``relevance_score`` (Exigence 10.3)."""
    service = _FakeSourceService()
    service.proposals.add(1)
    service.sources[1] = _Row(
        id=1,
        title="Source",
        url=None,
        publisher=None,
        source_type="OTHER",
        publication_date=None,
        is_verified=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/proposals/1/sources",
        json={"source_id": 1, "relevance_score": 0.75, "note": "Pertinent"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["proposal_id"] == 1
    assert body["source_id"] == 1
    assert body["relevance_score"] == 0.75


def test_attach_source_returns_404_for_missing_proposal() -> None:
    """POST /proposals/{id}/sources ⇒ 404 si la Proposition n'existe pas (Exigence 10.3)."""
    service = _FakeSourceService()
    service.sources[1] = _Row(
        id=1,
        title="Source",
        url=None,
        publisher=None,
        source_type="OTHER",
        publication_date=None,
        is_verified=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/proposals/999/sources",
        json={"source_id": 1, "relevance_score": 0.5},
    )

    assert resp.status_code == 404


def test_attach_source_returns_404_for_missing_source() -> None:
    """POST /proposals/{id}/sources ⇒ 404 si la Source n'existe pas (Exigence 10.3)."""
    service = _FakeSourceService()
    service.proposals.add(1)
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/proposals/1/sources",
        json={"source_id": 999, "relevance_score": 0.5},
    )

    assert resp.status_code == 404


def test_attach_source_rejects_relevance_score_out_of_range_with_422() -> None:
    """Un ``relevance_score`` hors ``[0, 1]`` ⇒ 422 (validation Pydantic, Exigence 10.3)."""
    service = _FakeSourceService()
    service.proposals.add(1)
    service.sources[1] = _Row(
        id=1,
        title="Source",
        url=None,
        publisher=None,
        source_type="OTHER",
        publication_date=None,
        is_verified=False,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/proposals/1/sources",
        json={"source_id": 1, "relevance_score": 1.5},
    )

    assert resp.status_code == 422
