# Feature: partipolia-platform, Property 2: Round-trip de retrait de Vote
"""Test de propriété : round-trip de retrait de Vote (Exigence 6.4).

**Property 2 — Round-trip de retrait de Vote**

**Validates: Requirements 6.4**

Pour tout couple (Utilisateur, Proposition) et toute valeur ``v ∈ {+1, 0, -1}``,
appliquer ``cast(v)`` puis ``withdraw`` :

1. ne laisse **aucun** Vote pour ce couple (Exigence 6.4 : le Vote retiré est
   supprimé et exclu des décomptes) ; et
2. restitue des décomptes (``support_count``, ``oppose_count``,
   ``participation_count``) **identiques** à l'état d'avant le ``cast``.

Autrement dit, ``withdraw`` est l'inverse exact de ``cast`` du point de vue des
décomptes agrégés : le retrait annule intégralement l'effet de l'enregistrement.

Environnement de test
---------------------
Le vrai code de :class:`~app.services.vote_service.VoteService` (``cast`` →
UPSERT, ``withdraw`` → DELETE, ``counts`` → agrégation) est exercé tel quel. La
couche d'accès aux données est fournie par une ``AsyncSession`` **factice en
mémoire** (:class:`_InMemoryVoteSession`) qui modélise fidèlement le contrat que
le Service attend de PostgreSQL :

* un UPSERT sur la contrainte ``UNIQUE(proposal_id, user_id)`` (un seul Vote par
  couple, remplacement de la valeur à la revote) ;
* un DELETE ciblé du Vote d'un couple ;
* une requête agrégée renvoyant ``(support, oppose, neutral, total)``.

Cette session factice ne remplace aucune logique métier : elle joue uniquement le
rôle du magasin de Votes, dans le même esprit que les tests existants du
``PopularityService``. Elle évite toute dépendance à un serveur PostgreSQL réel.
"""

from __future__ import annotations

from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.models.user import User
from app.models.vote import Vote
from app.services.vote_service import VoteService

# Valeurs de vote autorisées : soutien (+1), neutre (0), opposition (-1).
_VOTE_VALUES = (-1, 0, 1)


class _AggregateRow:
    """Ligne agrégée factice exposant ``one()`` (comme le résultat SQLAlchemy).

    Renvoie le quadruplet ``(support, oppose, neutral, total)`` attendu par
    ``VoteService.counts``.
    """

    def __init__(self, row: tuple[int, int, int, int]) -> None:
        self._row = row

    def one(self) -> tuple[int, int, int, int]:
        return self._row


class _InMemoryVoteSession:
    """``AsyncSession`` factice modélisant le magasin de Votes en mémoire.

    Le magasin est indexé par la clé ``(proposal_id, user_id)`` — matérialisant
    la contrainte ``UNIQUE(proposal_id, user_id)`` (Exigence 6.2). Chaque méthode
    reproduit exactement le comportement que ``VoteService`` attend de la base :

    * :meth:`execute` d'un ``INSERT ... ON CONFLICT DO UPDATE`` réalise un UPSERT ;
    * :meth:`execute` d'un ``SELECT`` agrégé calcule les décomptes ;
    * :meth:`scalar` d'un ``SELECT Vote WHERE ...`` retrouve le Vote d'un couple ;
    * :meth:`delete` supprime le Vote d'un couple ;
    * :meth:`flush` est neutre (aucune connexion réelle).
    """

    def __init__(self) -> None:
        self._store: dict[tuple[int, int], Vote] = {}
        self._next_id = 1

    # -- Introspection de test ------------------------------------------- #
    def has_vote(self, proposal_id: int, user_id: int) -> bool:
        return (proposal_id, user_id) in self._store

    def seed(self, proposal_id: int, user_id: int, value: int) -> None:
        """Insère directement un Vote préexistant (état initial du magasin)."""
        self._put(proposal_id, user_id, value)

    def _put(self, proposal_id: int, user_id: int, value: int) -> None:
        key = (proposal_id, user_id)
        existing = self._store.get(key)
        if existing is None:
            self._store[key] = Vote(
                id=self._next_id,
                proposal_id=proposal_id,
                user_id=user_id,
                value=value,
            )
            self._next_id += 1
        else:
            # UPSERT : remplacement de la valeur, sans nouvelle ligne (Exigence 6.3).
            existing.value = value

    # -- Contrat AsyncSession utilisé par VoteService -------------------- #
    async def execute(self, statement: Any) -> _AggregateRow:
        params = statement.compile().params
        if statement.is_insert:
            # cast() : UPSERT sur (proposal_id, user_id).
            self._put(params["proposal_id"], params["user_id"], params["value"])
            return _AggregateRow((0, 0, 0, 0))  # valeur ignorée par cast()
        # counts() : SELECT agrégé filtré par proposal_id (clé ``proposal_id_1``).
        proposal_id = params["proposal_id_1"]
        support = oppose = neutral = 0
        for (pid, _uid), vote in self._store.items():
            if pid != proposal_id:
                continue
            if vote.value == 1:
                support += 1
            elif vote.value == -1:
                oppose += 1
            else:
                neutral += 1
        total = support + oppose + neutral
        return _AggregateRow((support, oppose, neutral, total))

    async def scalar(self, statement: Any) -> Vote | None:
        # withdraw() : SELECT du Vote d'un couple (proposal_id, user_id).
        params = statement.compile().params
        key = (params["proposal_id_1"], params["user_id_1"])
        return self._store.get(key)

    async def delete(self, instance: Vote) -> None:
        self._store.pop((instance.proposal_id, instance.user_id), None)

    async def flush(self) -> None:  # pragma: no cover - opération neutre
        return None


def _user(user_id: int) -> User:
    """Construit un ``User`` minimal porteur d'un ``id`` (seul attribut lu)."""
    return User(
        id=user_id,
        email=f"user{user_id}@partipolia.local",
        password_hash="x",
        display_name=f"User {user_id}",
    )


# Votes préexistants d'AUTRES Utilisateurs : liste de (user_id, value) à user_id
# distincts, servant d'état de fond sur la Proposition.
_other_votes = st.lists(
    st.tuples(st.integers(min_value=1, max_value=10_000), st.sampled_from(_VOTE_VALUES)),
    max_size=8,
    unique_by=lambda pair: pair[0],
)


@pytest.mark.property
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    proposal_id=st.integers(min_value=1, max_value=10_000),
    target_user_id=st.integers(min_value=1, max_value=10_000),
    value=st.sampled_from(_VOTE_VALUES),
    other_votes=_other_votes,
)
async def test_cast_then_withdraw_is_a_noop_round_trip(
    proposal_id: int,
    target_user_id: int,
    value: int,
    other_votes: list[tuple[int, int]],
) -> None:
    """cast(v) puis withdraw ⇒ aucun Vote du couple et décomptes inchangés (Exigence 6.4)."""
    session = _InMemoryVoteSession()

    # État de fond : des Votes d'autres Utilisateurs (l'Utilisateur cible n'en a
    # aucun avant le cast, condition du round-trip « aucun Vote restant »).
    for other_user_id, other_value in other_votes:
        if other_user_id == target_user_id:
            continue
        session.seed(proposal_id, other_user_id, other_value)

    service = VoteService(session)  # type: ignore[arg-type]
    user = _user(target_user_id)

    # Décomptes AVANT le cast (état de référence).
    before = await service.counts(proposal_id)
    assert not session.has_vote(proposal_id, target_user_id)

    # cast(v) puis withdraw : le round-trip doit tout annuler.
    await service.cast(user, proposal_id, value)
    after = await service.withdraw(user, proposal_id)

    # 1) Aucun Vote ne subsiste pour le couple (Exigence 6.4).
    assert not session.has_vote(proposal_id, target_user_id)

    # 2) Les décomptes reviennent exactement à l'état d'avant le cast.
    assert after.support_count == before.support_count
    assert after.oppose_count == before.oppose_count
    assert after.participation_count == before.participation_count
    # Cohérence supplémentaire : le NEUTRE est aussi restitué à l'identique.
    assert after.neutral_count == before.neutral_count
    assert after.proposal_id == proposal_id


@pytest.mark.property
@settings(max_examples=200, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    proposal_id=st.integers(min_value=1, max_value=10_000),
    target_user_id=st.integers(min_value=1, max_value=10_000),
    value=st.sampled_from(_VOTE_VALUES),
)
async def test_withdraw_after_cast_removes_only_that_pair(
    proposal_id: int,
    target_user_id: int,
    value: int,
) -> None:
    """Le retrait n'affecte que le couple visé, même sans autre Vote (Exigence 6.4)."""
    session = _InMemoryVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]
    user = _user(target_user_id)

    counts_after_cast = await service.cast(user, proposal_id, value)
    assert session.has_vote(proposal_id, target_user_id)
    assert counts_after_cast.participation_count == 1

    counts_after_withdraw = await service.withdraw(user, proposal_id)
    assert not session.has_vote(proposal_id, target_user_id)
    assert counts_after_withdraw.support_count == 0
    assert counts_after_withdraw.oppose_count == 0
    assert counts_after_withdraw.neutral_count == 0
    assert counts_after_withdraw.participation_count == 0
