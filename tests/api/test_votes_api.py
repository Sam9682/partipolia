"""Tests d'API des Votes (Exigence 6, tâche 5.4).

Ces tests exercent la couche HTTP de ``app/api/v1/votes.py`` (câblage des points
d'accès, codes de statut, garde d'authentification et sérialisation) sans base de
données : le :class:`~app.services.vote_service.VoteService` est remplacé par une
implémentation factice qui reproduit son contrat (UPSERT sur
``UNIQUE(proposal_id, user_id)``, retrait, décomptes) en mémoire. La vraie logique
du Service est couverte par ailleurs (``tests/unit`` et tests de propriété) ; on
valide ici uniquement le comportement de l'API :

* ``POST /proposals/{id}/votes`` — ``200`` + décomptes ; ``401`` si anonyme
  (Exigences 6.1, 6.6) ; ``422`` si ``value`` hors ``{+1, 0, -1}`` (validation
  Pydantic) ;
* ``DELETE /proposals/{id}/votes`` — ``204`` ; ``401`` si anonyme (Exigences 6.4,
  6.6) ;
* ``GET /proposals/{id}/votes`` — ``200`` + décomptes, consultation publique
  (Exigence 6.5).

Le montage suit exactement celui de production : le routeur ``votes.router`` est
inclus sous le préfixe ``/proposals`` (comme dans ``app.api.v1.router``).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.api.v1 import votes
from app.schemas.auth import UserPublic
from app.schemas.vote import VoteCounts

pytestmark = pytest.mark.api


class _FakeVoteService:
    """``VoteService`` factice reproduisant le contrat de vote en mémoire.

    Les votes sont indexés par ``(proposal_id, user_id)`` pour honorer l'unicité
    et l'UPSERT ; ``counts`` agrège les valeurs comme la vraie requête SQL.
    """

    def __init__(self) -> None:
        self._votes: dict[tuple[int, int], int] = {}

    async def cast(self, user: UserPublic, proposal_id: int, value: int) -> VoteCounts:
        self._votes[(proposal_id, user.id)] = value
        return await self.counts(proposal_id)

    async def withdraw(self, user: UserPublic, proposal_id: int) -> VoteCounts:
        self._votes.pop((proposal_id, user.id), None)
        return await self.counts(proposal_id)

    async def counts(self, proposal_id: int) -> VoteCounts:
        values = [v for (pid, _uid), v in self._votes.items() if pid == proposal_id]
        return VoteCounts(
            proposal_id=proposal_id,
            support_count=sum(1 for v in values if v == 1),
            oppose_count=sum(1 for v in values if v == -1),
            neutral_count=sum(1 for v in values if v == 0),
            participation_count=len(values),
        )


def _user(user_id: int = 1) -> UserPublic:
    """Construit un ``UserPublic`` minimal pour les tests authentifiés."""
    now = datetime.now(UTC)
    return UserPublic(
        id=user_id,
        email="citoyen@example.org",
        display_name="Citoyen",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=now,
        updated_at=now,
    )


def _build_app(service: _FakeVoteService, *, authenticated: bool) -> FastAPI:
    """Assemble une application ne montant que le routeur des Votes.

    Le service factice et, si ``authenticated``, un utilisateur courant sont
    injectés via les surcharges de dépendances. Sans surcharge d'auth, la garde
    :func:`get_current_user` s'applique et refuse (``401``) les points protégés.
    """
    app = FastAPI()
    app.include_router(votes.router, prefix="/proposals", tags=["votes"])
    app.dependency_overrides[votes.get_vote_service] = lambda: service
    if authenticated:
        app.dependency_overrides[get_current_user] = _user
    return app


def _client(service: _FakeVoteService, *, authenticated: bool) -> TestClient:
    return TestClient(_build_app(service, authenticated=authenticated))


# --------------------------------------------------------------------------- #
# POST /proposals/{id}/votes                                                   #
# --------------------------------------------------------------------------- #
def test_cast_vote_returns_200_and_counts() -> None:
    """Voter renvoie ``200`` et les décomptes à jour (Exigence 6.1)."""
    client = _client(_FakeVoteService(), authenticated=True)

    response = client.post("/proposals/7/votes", json={"value": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["proposal_id"] == 7
    assert body["support_count"] == 1
    assert body["participation_count"] == 1


def test_revote_replaces_value_without_adding_row() -> None:
    """Un revote remplace la valeur sans créer de Vote supplémentaire (Exigence 6.3)."""
    client = _client(_FakeVoteService(), authenticated=True)

    client.post("/proposals/7/votes", json={"value": 1})
    response = client.post("/proposals/7/votes", json={"value": -1})

    assert response.status_code == 200
    body = response.json()
    assert body["support_count"] == 0
    assert body["oppose_count"] == 1
    assert body["participation_count"] == 1


def test_cast_vote_requires_authentication() -> None:
    """Une tentative de vote anonyme est refusée par un ``401`` (Exigence 6.6)."""
    client = _client(_FakeVoteService(), authenticated=False)

    response = client.post("/proposals/7/votes", json={"value": 1})

    assert response.status_code == 401


@pytest.mark.parametrize("value", [2, -2, 5, 42])
def test_cast_vote_rejects_out_of_domain_value(value: int) -> None:
    """Une valeur hors ``{+1, 0, -1}`` est rejetée par la validation (Exigence 6.1)."""
    client = _client(_FakeVoteService(), authenticated=True)

    response = client.post("/proposals/7/votes", json={"value": value})

    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# DELETE /proposals/{id}/votes                                                 #
# --------------------------------------------------------------------------- #
def test_withdraw_vote_returns_204() -> None:
    """Le retrait d'un Vote renvoie ``204`` et l'exclut des décomptes (Exigence 6.4)."""
    service = _FakeVoteService()
    client = _client(service, authenticated=True)

    client.post("/proposals/7/votes", json={"value": 1})
    response = client.delete("/proposals/7/votes")

    assert response.status_code == 204
    # Le décompte public confirme l'exclusion du Vote retiré.
    counts = client.get("/proposals/7/votes").json()
    assert counts["participation_count"] == 0


def test_withdraw_vote_is_idempotent() -> None:
    """Retirer un Vote inexistant ne provoque pas d'erreur (Exigence 6.4)."""
    client = _client(_FakeVoteService(), authenticated=True)

    response = client.delete("/proposals/7/votes")

    assert response.status_code == 204


def test_withdraw_vote_requires_authentication() -> None:
    """Le retrait anonyme est refusé par un ``401`` (Exigence 6.6)."""
    client = _client(_FakeVoteService(), authenticated=False)

    response = client.delete("/proposals/7/votes")

    assert response.status_code == 401


# --------------------------------------------------------------------------- #
# GET /proposals/{id}/votes                                                    #
# --------------------------------------------------------------------------- #
def test_get_counts_is_public() -> None:
    """La consultation des décomptes est publique (Exigence 6.5)."""
    client = _client(_FakeVoteService(), authenticated=False)

    response = client.get("/proposals/7/votes")

    assert response.status_code == 200
    body = response.json()
    assert body["proposal_id"] == 7
    assert body["participation_count"] == 0


def test_get_counts_reflects_cast_votes() -> None:
    """Les décomptes publics reflètent les Votes enregistrés (Exigences 6.1, 6.5)."""
    service = _FakeVoteService()
    authed = _client(service, authenticated=True)
    public = _client(service, authenticated=False)

    authed.post("/proposals/7/votes", json={"value": 1})

    body = public.get("/proposals/7/votes").json()
    assert body["support_count"] == 1
    assert body["participation_count"] == 1
