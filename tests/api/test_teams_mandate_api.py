"""Tests d'API des Équipes et du suivi de mandat (Exigences 20, 21 ; tâche 8.3).

Ces tests exercent les points d'accès de ``app/api/v1/teams.py`` et
``app/api/v1/mandate.py`` via un client HTTP (``fastapi.testclient.TestClient``)
en **remplaçant** deux dépendances par des doublures :

* les fabriques de Services (``get_team_service`` / ``get_mandate_service``) →
  des doublures reproduisant le contrat public **sans base de données** ;
* :func:`app.api.deps.require_admin` → un Administrateur fixe (ou omis pour
  vérifier le refus ``401`` sur les actions d'administration).

Seule la couche de persistance est remplacée : le vrai code de routage, de
sérialisation Pydantic et de traduction des exceptions en codes HTTP est exercé.

Couverture :

* ``GET /teams`` — liste publique des Équipes et membres (Exigences 20.1, 20.2) ;
* ``POST /teams`` — création réservée à l'Administrateur (``401`` sans admin,
  Exigence 20.3) ;
* ``POST /teams/{id}/members`` — ajout d'un membre, ``404`` si Équipe absente
  (Exigences 20.2, 20.3) ;
* ``GET /mandate/commitments`` / ``/mandate/indicators`` — listes publiques
  (Exigences 21.1, 21.3) ;
* ``PUT /mandate/commitments`` — statut invalide ⇒ ``422`` (validation Pydantic
  de l'énumération, Exigence 21.2) ;
* ``PATCH /mandate/indicators/{id}`` — mise à jour de valeur, ``404`` si absent
  (Exigence 21.4).
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
from app.api.v1.mandate import get_mandate_service  # noqa: E402
from app.api.v1.mandate import router as mandate_router  # noqa: E402
from app.api.v1.teams import get_team_service  # noqa: E402
from app.api.v1.teams import router as teams_router  # noqa: E402
from app.schemas.auth import UserPublic  # noqa: E402
from app.services.mandate_service import (  # noqa: E402
    CommitmentNotFoundError,
    IndicatorNotFoundError,
    InvalidCommitmentStatusError,
)
from app.services.team_service import TeamNotFoundError  # noqa: E402

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _Row:
    """Objet minimal exposant les attributs lus par les schémas (from_attributes)."""

    def __init__(self, **attrs: Any) -> None:
        for key, value in attrs.items():
            setattr(self, key, value)


class _FakeTeamService:
    def __init__(self) -> None:
        self.teams: dict[int, _Row] = {}
        self._next_team = 1
        self._next_member = 1

    async def list_teams(self) -> list[_Row]:
        return list(self.teams.values())

    async def create_team(self, name: str) -> _Row:
        team = _Row(
            id=self._next_team,
            name=name,
            created_at=_NOW,
            updated_at=_NOW,
            members=[],
        )
        self.teams[team.id] = team
        self._next_team += 1
        return team

    async def add_member(
        self,
        team_id: int,
        *,
        user_id: int,
        role: str | None = None,
        bio: str | None = None,
    ) -> _Row:
        if team_id not in self.teams:
            raise TeamNotFoundError(str(team_id))
        member = _Row(
            id=self._next_member,
            team_id=team_id,
            user_id=user_id,
            role=role,
            bio=bio,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self._next_member += 1
        return member


class _FakeMandateService:
    def __init__(self) -> None:
        self.commitments: dict[int, _Row] = {}
        self.indicators: dict[int, _Row] = {}
        self._next_commitment = 1

    async def list_commitments(self) -> list[_Row]:
        return list(self.commitments.values())

    async def list_indicators(self) -> list[_Row]:
        return list(self.indicators.values())

    async def upsert_commitment(
        self,
        *,
        commitment_id: int | None = None,
        title: str,
        status: str,
        target_date: date | None = None,
        progress: float | None = None,
        notes: str | None = None,
    ) -> _Row:
        if status not in {
            "NOT_STARTED",
            "IN_PROGRESS",
            "COMPLETED",
            "MODIFIED",
            "ABANDONED",
        }:
            raise InvalidCommitmentStatusError(status)
        if commitment_id is not None and commitment_id not in self.commitments:
            raise CommitmentNotFoundError(str(commitment_id))
        cid = commitment_id or self._next_commitment
        if commitment_id is None:
            self._next_commitment += 1
        row = _Row(
            id=cid,
            title=title,
            status=status,
            target_date=target_date,
            progress=progress,
            notes=notes,
            created_at=_NOW,
            updated_at=_NOW,
        )
        self.commitments[cid] = row
        return row

    async def update_indicator(
        self, indicator_id: int, *, current_value: float
    ) -> _Row:
        if indicator_id not in self.indicators:
            raise IndicatorNotFoundError(str(indicator_id))
        row = self.indicators[indicator_id]
        row.current_value = current_value
        return row


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


def _build_teams_client(
    service: _FakeTeamService, *, admin: UserPublic | None
) -> TestClient:
    app = FastAPI()
    app.include_router(teams_router, prefix="/api/v1/teams")
    app.dependency_overrides[get_team_service] = lambda: service
    if admin is not None:
        app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app, raise_server_exceptions=True)


def _build_mandate_client(
    service: _FakeMandateService, *, admin: UserPublic | None
) -> TestClient:
    app = FastAPI()
    app.include_router(mandate_router, prefix="/api/v1/mandate")
    app.dependency_overrides[get_mandate_service] = lambda: service
    if admin is not None:
        app.dependency_overrides[require_admin] = lambda: admin
    return TestClient(app, raise_server_exceptions=True)


# --------------------------------------------------------------------------- #
# Équipes (Exigence 20)                                                         #
# --------------------------------------------------------------------------- #
def test_list_teams_is_public_and_returns_members() -> None:
    """GET /teams est public et renvoie les Équipes avec leurs membres (Exigences 20.1, 20.2)."""
    service = _FakeTeamService()
    service.teams[1] = _Row(
        id=1,
        name="Équipe A",
        created_at=_NOW,
        updated_at=_NOW,
        members=[
            _Row(
                id=1,
                team_id=1,
                user_id=5,
                role="Porte-parole",
                bio="Bio",
                created_at=_NOW,
                updated_at=_NOW,
            )
        ],
    )
    client = _build_teams_client(service, admin=None)

    resp = client.get("/api/v1/teams")

    assert resp.status_code == 200
    body = resp.json()
    assert body[0]["name"] == "Équipe A"
    assert body[0]["members"][0]["role"] == "Porte-parole"


def test_create_team_requires_admin() -> None:
    """Sans Administrateur, POST /teams renvoie 401 (Exigence 20.3)."""
    service = _FakeTeamService()
    client = _build_teams_client(service, admin=None)

    resp = client.post("/api/v1/teams", json={"name": "Nouvelle"})

    assert resp.status_code == 401


def test_create_team_as_admin_returns_201() -> None:
    """POST /teams par un Administrateur crée l'Équipe (201) (Exigence 20.3)."""
    service = _FakeTeamService()
    client = _build_teams_client(service, admin=_admin())

    resp = client.post("/api/v1/teams", json={"name": "Nouvelle"})

    assert resp.status_code == 201
    assert resp.json()["name"] == "Nouvelle"


def test_add_member_returns_404_for_missing_team() -> None:
    """POST /teams/{id}/members ⇒ 404 si l'Équipe n'existe pas (Exigences 20.2, 20.3)."""
    service = _FakeTeamService()
    client = _build_teams_client(service, admin=_admin())

    resp = client.post(
        "/api/v1/teams/999/members",
        json={"user_id": 3, "role": "Trésorier", "bio": "Bio"},
    )

    assert resp.status_code == 404


def test_add_member_as_admin_returns_201() -> None:
    """POST /teams/{id}/members ajoute un membre avec role/bio (Exigences 20.2, 20.3)."""
    service = _FakeTeamService()
    client = _build_teams_client(service, admin=_admin())
    team = client.post("/api/v1/teams", json={"name": "Équipe"}).json()

    resp = client.post(
        f"/api/v1/teams/{team['id']}/members",
        json={"user_id": 3, "role": "Trésorier", "bio": "Bio"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["role"] == "Trésorier"
    assert body["user_id"] == 3


# --------------------------------------------------------------------------- #
# Mandat (Exigence 21)                                                          #
# --------------------------------------------------------------------------- #
def test_list_commitments_and_indicators_are_public() -> None:
    """GET /mandate/commitments et /mandate/indicators sont publics (Exigences 21.1, 21.3)."""
    service = _FakeMandateService()
    service.commitments[1] = _Row(
        id=1,
        title="Objectif",
        status="IN_PROGRESS",
        target_date=None,
        progress=42.0,
        notes=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    service.indicators[1] = _Row(
        id=1,
        name="Chômage",
        unit="%",
        baseline=8.0,
        target=6.0,
        current_value=7.5,
        source_id=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_mandate_client(service, admin=None)

    commitments = client.get("/api/v1/mandate/commitments")
    indicators = client.get("/api/v1/mandate/indicators")

    assert commitments.status_code == 200
    assert commitments.json()[0]["status"] == "IN_PROGRESS"
    assert indicators.status_code == 200
    assert indicators.json()[0]["current_value"] == 7.5


def test_upsert_commitment_rejects_invalid_status_with_422() -> None:
    """Un ``status`` hors énumération ⇒ 422 (validation Pydantic, Exigence 21.2)."""
    service = _FakeMandateService()
    client = _build_mandate_client(service, admin=_admin())

    resp = client.put(
        "/api/v1/mandate/commitments",
        json={"title": "Objectif", "status": "DONE"},
    )

    assert resp.status_code == 422


def test_upsert_commitment_creates_as_admin() -> None:
    """PUT /mandate/commitments crée un Engagement (Administrateur) (Exigences 21.1, 21.2)."""
    service = _FakeMandateService()
    client = _build_mandate_client(service, admin=_admin())

    resp = client.put(
        "/api/v1/mandate/commitments",
        json={"title": "Objectif", "status": "COMPLETED"},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "COMPLETED"


def test_upsert_commitment_requires_admin() -> None:
    """Sans Administrateur, PUT /mandate/commitments renvoie 401 (Exigence 21.4)."""
    service = _FakeMandateService()
    client = _build_mandate_client(service, admin=None)

    resp = client.put(
        "/api/v1/mandate/commitments",
        json={"title": "Objectif", "status": "COMPLETED"},
    )

    assert resp.status_code == 401


def test_update_indicator_returns_404_for_missing() -> None:
    """PATCH /mandate/indicators/{id} ⇒ 404 si l'Indicateur n'existe pas (Exigence 21.4)."""
    service = _FakeMandateService()
    client = _build_mandate_client(service, admin=_admin())

    resp = client.patch(
        "/api/v1/mandate/indicators/999", json={"current_value": 3.0}
    )

    assert resp.status_code == 404


def test_update_indicator_as_admin_updates_value() -> None:
    """PATCH /mandate/indicators/{id} met à jour ``current_value`` (Exigence 21.4)."""
    service = _FakeMandateService()
    service.indicators[1] = _Row(
        id=1,
        name="Chômage",
        unit="%",
        baseline=8.0,
        target=6.0,
        current_value=7.5,
        source_id=None,
        created_at=_NOW,
        updated_at=_NOW,
    )
    client = _build_mandate_client(service, admin=_admin())

    resp = client.patch("/api/v1/mandate/indicators/1", json={"current_value": 7.0})

    assert resp.status_code == 200
    assert resp.json()["current_value"] == 7.0
