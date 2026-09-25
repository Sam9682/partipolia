"""Tests unitaires du ``VoteService`` (Exigence 6).

Couvre le cœur métier du Service par des exemples déterministes, sans base de
données réelle — en complément des tests de propriété
(``test_vote_service_property.py`` sur PostgreSQL, ``test_vote_service_properties.py``
en mémoire). Ces tests unitaires fournissent une couverture au niveau exemple qui
s'exécute **hors ligne** (le test de la Property 1 dépend d'un ``UPSERT``
PostgreSQL et est ignoré en l'absence de base joignable).

Points couverts :

* ``cast`` insère un Vote pour un nouveau couple (Utilisateur, Proposition)
  (Exigences 6.1, 6.2) ;
* ``cast`` réappliqué au même couple **remplace** la valeur sans créer de ligne
  supplémentaire — unicité et idempotence de la revote (Exigences 6.2, 6.3) ;
* ``cast`` refuse une valeur hors de ``{+1, 0, -1}`` (Exigence 6.1) ;
* ``withdraw`` supprime le Vote du couple et l'exclut des décomptes ; il est
  idempotent (retirer un Vote inexistant ne lève pas d'erreur) (Exigence 6.4) ;
* ``counts`` agrège correctement soutien / opposition / neutre / participation
  sur plusieurs Utilisateurs (Exigence 6.5).

Comme pour ``test_argument_service.py`` et ``test_comment_service.py``, seule la
couche d'accès aux données est remplacée par une ``AsyncSession`` factice en
mémoire ; aucun code du Service n'est mocké. La session factice modélise
fidèlement le contrat attendu de PostgreSQL : un magasin indexé par la clé
``(proposal_id, user_id)`` (contrainte ``UNIQUE(proposal_id, user_id)``), un
``UPSERT`` sur cette clé et une requête agrégée ``(support, oppose, neutral,
total)``.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models.user import User
from app.models.vote import Vote
from app.services.vote_service import INVALID_VOTE_VALUE_ERROR, VoteError, VoteService


class _AggregateRow:
    """Ligne agrégée factice exposant ``one()`` (comme le résultat SQLAlchemy).

    Renvoie le quadruplet ``(support, oppose, neutral, total)`` attendu par
    ``VoteService.counts``.
    """

    def __init__(self, row: tuple[int, int, int, int]) -> None:
        self._row = row

    def one(self) -> tuple[int, int, int, int]:
        return self._row


class _FakeVoteSession:
    """``AsyncSession`` factice modélisant le magasin de Votes en mémoire.

    Le magasin est indexé par ``(proposal_id, user_id)`` — matérialisant la
    contrainte ``UNIQUE(proposal_id, user_id)`` (Exigence 6.2). Chaque méthode
    reproduit exactement le comportement que ``VoteService`` attend de la base :

    * :meth:`execute` d'un ``INSERT ... ON CONFLICT DO UPDATE`` réalise l'UPSERT ;
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

    def row_count(self, proposal_id: int, user_id: int) -> int:
        return 1 if (proposal_id, user_id) in self._store else 0

    def stored_value(self, proposal_id: int, user_id: int) -> int | None:
        vote = self._store.get((proposal_id, user_id))
        return None if vote is None else vote.value

    def seed(self, proposal_id: int, user_id: int, value: int) -> None:
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

    async def flush(self) -> None:
        return None


def _user(user_id: int) -> User:
    """Construit un ``User`` minimal porteur d'un ``id`` (seul attribut lu)."""
    return User(
        id=user_id,
        email=f"user{user_id}@partipolia.local",
        password_hash="x",
        display_name=f"User {user_id}",
    )


# --------------------------------------------------------------------------- #
# cast — insertion, UPSERT/unicité, valeurs autorisées (Exigences 6.1–6.3)     #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, (1, 0, 0, 1)),
        (-1, (0, 1, 0, 1)),
        (0, (0, 0, 1, 1)),
    ],
)
async def test_cast_inserts_vote_for_new_pair(
    value: int, expected: tuple[int, int, int, int]
) -> None:
    """``cast`` insère un Vote et renvoie les décomptes du couple (Exigences 6.1, 6.2)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]

    counts = await service.cast(_user(7), 1, value)

    assert session.row_count(1, 7) == 1
    assert session.stored_value(1, 7) == value
    support, oppose, neutral, participation = expected
    assert counts.support_count == support
    assert counts.oppose_count == oppose
    assert counts.neutral_count == neutral
    assert counts.participation_count == participation
    assert counts.proposal_id == 1


@pytest.mark.unit
async def test_recast_replaces_value_without_new_row() -> None:
    """Une revote remplace la valeur sans créer de seconde ligne (Exigences 6.2, 6.3)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]
    user = _user(7)

    await service.cast(user, 1, 1)
    assert session.row_count(1, 7) == 1
    assert session.stored_value(1, 7) == 1

    # Revote sur le même couple : la valeur est remplacée, pas dupliquée.
    counts = await service.cast(user, 1, -1)

    assert session.row_count(1, 7) == 1
    assert session.stored_value(1, 7) == -1
    assert counts.support_count == 0
    assert counts.oppose_count == 1
    assert counts.participation_count == 1


@pytest.mark.unit
@pytest.mark.parametrize("value", [2, -2, 3, 42, -100])
async def test_cast_rejects_value_outside_domain(value: int) -> None:
    """Une valeur hors ``{+1, 0, -1}`` est refusée sans écriture (Exigence 6.1)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]

    with pytest.raises(VoteError) as excinfo:
        await service.cast(_user(7), 1, value)

    assert str(excinfo.value) == INVALID_VOTE_VALUE_ERROR
    assert session.row_count(1, 7) == 0


# --------------------------------------------------------------------------- #
# withdraw — retrait et idempotence (Exigence 6.4)                             #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_withdraw_removes_vote_and_excludes_from_counts() -> None:
    """``withdraw`` supprime le Vote et l'exclut des décomptes (Exigence 6.4)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]
    user = _user(7)

    await service.cast(user, 1, 1)
    assert session.has_vote(1, 7)

    counts = await service.withdraw(user, 1)

    assert not session.has_vote(1, 7)
    assert counts.support_count == 0
    assert counts.oppose_count == 0
    assert counts.neutral_count == 0
    assert counts.participation_count == 0


@pytest.mark.unit
async def test_withdraw_is_idempotent_when_no_vote_exists() -> None:
    """Retirer un Vote inexistant ne lève pas d'erreur (Exigence 6.4)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]

    counts = await service.withdraw(_user(7), 1)

    assert not session.has_vote(1, 7)
    assert counts.participation_count == 0
    assert counts.proposal_id == 1


@pytest.mark.unit
async def test_withdraw_only_affects_the_targeted_pair() -> None:
    """Le retrait ne touche que le couple visé, pas les autres Votes (Exigence 6.4)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]

    await service.cast(_user(7), 1, 1)
    await service.cast(_user(8), 1, -1)

    counts = await service.withdraw(_user(7), 1)

    assert not session.has_vote(1, 7)
    assert session.has_vote(1, 8)
    assert session.stored_value(1, 8) == -1
    assert counts.oppose_count == 1
    assert counts.support_count == 0
    assert counts.participation_count == 1


# --------------------------------------------------------------------------- #
# counts — agrégation multi-utilisateurs (Exigence 6.5)                        #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_counts_aggregates_across_users() -> None:
    """``counts`` agrège soutien / opposition / neutre / participation (Exigence 6.5)."""
    session = _FakeVoteSession()
    # 3 soutiens, 2 oppositions, 1 neutre sur la Proposition 1.
    session.seed(1, 1, 1)
    session.seed(1, 2, 1)
    session.seed(1, 3, 1)
    session.seed(1, 4, -1)
    session.seed(1, 5, -1)
    session.seed(1, 6, 0)
    # Un Vote sur une autre Proposition ne doit pas être compté.
    session.seed(2, 1, 1)
    service = VoteService(session)  # type: ignore[arg-type]

    counts = await service.counts(1)

    assert counts.support_count == 3
    assert counts.oppose_count == 2
    assert counts.neutral_count == 1
    assert counts.participation_count == 6
    assert counts.proposal_id == 1


@pytest.mark.unit
async def test_counts_is_zero_for_proposal_without_votes() -> None:
    """Une Proposition sans Vote renvoie des décomptes nuls (Exigence 6.5)."""
    session = _FakeVoteSession()
    service = VoteService(session)  # type: ignore[arg-type]

    counts = await service.counts(99)

    assert counts.support_count == 0
    assert counts.oppose_count == 0
    assert counts.neutral_count == 0
    assert counts.participation_count == 0
    assert counts.proposal_id == 99
