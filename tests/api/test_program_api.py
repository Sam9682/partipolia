"""Tests d'API du Programme (Exigences 18 et 19, tâche 8.1).

Ces tests exercent la couche HTTP de ``app/api/v1/program.py`` (câblage des
points d'accès, codes de statut, garde d'administration et sérialisation) sans
base de données : le :class:`~app.services.program_service.ProgramService` est
remplacé par une implémentation factice reproduisant son contrat en mémoire. La
vraie logique du Service est couverte par ``tests/unit/test_program_service.py`` ;
on valide ici uniquement le comportement de l'API :

* ``GET /program`` et ``GET /program/proposals`` et ``GET /program/statistics`` —
  consultation **publique** (Exigences 18.1, 18.5) ;
* ``POST /program/proposals`` — ``201`` (Administrateur) ; ``401`` si anonyme ;
  ``403`` si Utilisateur non-administrateur (Exigences 18.2, 18.4) ;
* ``DELETE /program/proposals/{id}`` — ``204`` (Administrateur) ; ``404`` si
  l'association n'existe pas (Exigence 18.3).

Le montage suit exactement celui de production : le routeur ``program.router`` est
inclus sous le préfixe ``/program`` (comme dans ``app.api.v1.router``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, require_admin
from app.api.v1 import program
from app.schemas.auth import UserPublic
from app.schemas.program import (
    ProgramProposalPublic,
    ProgramPublic,
    ProgramStatistics,
)
from app.services.program_service import (
    ProgramProposalNotFoundError,
    ProposalNotFoundError,
)

pytestmark = pytest.mark.api


class _FakeProgramService:
    """``ProgramService`` factice reproduisant le contrat du Programme en mémoire."""

    def __init__(self, *, known_proposals: set[int] | None = None) -> None:
        self._known = known_proposals if known_proposals is not None else {7}
        self._associations: dict[int, dict[str, object]] = {}
        self._next_id = 1

    async def get_program(self) -> ProgramPublic:
        return ProgramPublic(id=1, status="DRAFT", created_at=datetime.now(UTC))

    async def list_program_proposals(self) -> list[ProgramProposalPublic]:
        return [
            ProgramProposalPublic(
                id=data["id"],  # type: ignore[arg-type]
                program_id=1,
                proposal_id=pid,
                priority=data["priority"],  # type: ignore[arg-type]
                included_at=data["included_at"],  # type: ignore[arg-type]
            )
            for pid, data in sorted(
                self._associations.items(), key=lambda kv: kv[1]["priority"]
            )
        ]

    async def add_proposal(
        self, proposal_id: int, priority: int = 0
    ) -> ProgramProposalPublic:
        if proposal_id not in self._known:
            raise ProposalNotFoundError(str(proposal_id))
        assoc_id = self._next_id
        self._next_id += 1
        included_at = datetime.now(UTC)
        self._associations[proposal_id] = {
            "id": assoc_id,
            "priority": priority,
            "included_at": included_at,
        }
        return ProgramProposalPublic(
            id=assoc_id,
            program_id=1,
            proposal_id=proposal_id,
            priority=priority,
            included_at=included_at,
        )

    async def remove_proposal(self, proposal_id: int) -> None:
        if proposal_id not in self._associations:
            raise ProgramProposalNotFoundError(str(proposal_id))
        del self._associations[proposal_id]

    async def statistics(self) -> ProgramStatistics:
        return ProgramStatistics(
            program_id=1,
            included_count=len(self._associations),
            definitive_count=len(self._associations),
            theme_count=len(self._associations),
        )


def _user(*, is_admin: bool, user_id: int = 1) -> UserPublic:
    now = datetime.now(UTC)
    return UserPublic(
        id=user_id,
        email="citoyen@example.org",
        display_name="Citoyen",
        is_active=True,
        is_verified=True,
        is_admin=is_admin,
        created_at=now,
        updated_at=now,
    )


def _build_app(
    service: _FakeProgramService, *, role: str | None
) -> FastAPI:
    """Assemble une application ne montant que le routeur du Programme.

    ``role`` vaut ``"admin"`` (Administrateur), ``"user"`` (authentifié non-admin)
    ou ``None`` (anonyme). Sans surcharge d'auth, la garde
    :func:`require_admin` (via :func:`get_current_user`) refuse (``401``).
    """
    app = FastAPI()
    app.include_router(program.router, prefix="/program", tags=["program"])
    app.dependency_overrides[program.get_program_service] = lambda: service
    if role == "admin":
        app.dependency_overrides[get_current_user] = lambda: _user(is_admin=True)
    elif role == "user":
        app.dependency_overrides[get_current_user] = lambda: _user(is_admin=False)
    return app


def _client(service: _FakeProgramService, *, role: str | None) -> TestClient:
    return TestClient(_build_app(service, role=role))


# --------------------------------------------------------------------------- #
# Lectures publiques                                                           #
# --------------------------------------------------------------------------- #
def test_get_program_is_public() -> None:
    """``GET /program`` est public et renvoie le Programme (Exigence 18.1)."""
    client = _client(_FakeProgramService(), role=None)

    response = client.get("/program")

    assert response.status_code == 200
    assert response.json()["id"] == 1


def test_list_program_proposals_is_public() -> None:
    """``GET /program/proposals`` est public (Exigence 18.1)."""
    client = _client(_FakeProgramService(), role=None)

    response = client.get("/program/proposals")

    assert response.status_code == 200
    assert response.json() == []


def test_get_statistics_is_public() -> None:
    """``GET /program/statistics`` est public (Exigence 18.5)."""
    client = _client(_FakeProgramService(), role=None)

    response = client.get("/program/statistics")

    assert response.status_code == 200
    body = response.json()
    assert body["program_id"] == 1
    assert body["included_count"] == 0


# --------------------------------------------------------------------------- #
# POST /program/proposals — réservé à l'Administrateur                         #
# --------------------------------------------------------------------------- #
def test_add_proposal_requires_authentication() -> None:
    """L'ajout anonyme est refusé par un ``401`` (Exigences 18.2)."""
    client = _client(_FakeProgramService(), role=None)

    response = client.post("/program/proposals", json={"proposal_id": 7, "priority": 1})

    assert response.status_code == 401


def test_add_proposal_forbidden_for_non_admin() -> None:
    """L'ajout par un Utilisateur non-administrateur est refusé par un ``403`` (Exigence 18.2)."""
    client = _client(_FakeProgramService(), role="user")

    response = client.post("/program/proposals", json={"proposal_id": 7, "priority": 1})

    assert response.status_code == 403


def test_add_proposal_created_for_admin() -> None:
    """L'Administrateur ajoute une Proposition (``201``) avec ``priority`` et ``included_at`` (Exigences 18.2, 18.4)."""
    client = _client(_FakeProgramService(known_proposals={7}), role="admin")

    response = client.post("/program/proposals", json={"proposal_id": 7, "priority": 3})

    assert response.status_code == 201
    body = response.json()
    assert body["proposal_id"] == 7
    assert body["priority"] == 3
    assert body["included_at"] is not None


def test_add_unknown_proposal_returns_404() -> None:
    """Ajouter une Proposition inexistante renvoie ``404`` (Exigences 18.2, 31.2)."""
    client = _client(_FakeProgramService(known_proposals=set()), role="admin")

    response = client.post("/program/proposals", json={"proposal_id": 999, "priority": 0})

    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# DELETE /program/proposals/{id} — réservé à l'Administrateur                  #
# --------------------------------------------------------------------------- #
def test_remove_proposal_requires_admin() -> None:
    """Le retrait par un non-administrateur est refusé par un ``403`` (Exigence 18.3)."""
    client = _client(_FakeProgramService(), role="user")

    response = client.delete("/program/proposals/7")

    assert response.status_code == 403


def test_remove_proposal_returns_204_for_admin() -> None:
    """L'Administrateur retire une association existante (``204``) (Exigence 18.3)."""
    service = _FakeProgramService(known_proposals={7})
    client = _client(service, role="admin")

    client.post("/program/proposals", json={"proposal_id": 7, "priority": 0})
    response = client.delete("/program/proposals/7")

    assert response.status_code == 204


def test_remove_unknown_association_returns_404() -> None:
    """Retirer une association inexistante renvoie ``404`` (Exigences 18.3, 31.2)."""
    client = _client(_FakeProgramService(), role="admin")

    response = client.delete("/program/proposals/123")

    assert response.status_code == 404
