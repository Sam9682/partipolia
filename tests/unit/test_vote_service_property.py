# Feature: partipolia-platform, Property 1: Unicité et idempotence du Vote
"""Test de propriété — Unicité et idempotence du Vote (Property 1).

**Validates: Requirements 6.2, 6.3**

Propriété vérifiée (conception, section ``VoteService`` ; Exigences 6.2 et 6.3) :

    Pour tout couple (Utilisateur, Proposition) et toute séquence d'opérations de
    vote à valeurs dans ``{+1, 0, -1}``, après application de la séquence il existe
    **au plus une** ligne ``Vote`` pour ce couple, et sa ``value`` égale la
    **dernière** valeur soumise ; une revote **remplace** la valeur sans créer de
    ligne supplémentaire.

Le test exerce le **vrai** ``VoteService`` (UPSERT ``ON CONFLICT`` sur la
contrainte ``UNIQUE(proposal_id, user_id)``) sur une véritable base PostgreSQL —
seul dialecte implémentant cet UPSERT et SGBD de production (Exigences 32.2,
32.3). En l'absence de base joignable, le test est ignoré (voir
``tests/conftest.py``).

Hypothèses Hypothesis : ≥ 100 exemples (``max_examples=200``), séquences non vides
de valeurs de vote, exécutées de façon déterministe sur un couple (Utilisateur,
Proposition) réinitialisé à chaque exemple.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select

from app.models.proposal import Proposal
from app.models.theme import Theme
from app.models.user import User
from app.models.vote import Vote
from app.services.vote_service import VoteService

if TYPE_CHECKING:  # pragma: no cover
    from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = [pytest.mark.property, pytest.mark.integration]

# Valeurs de vote autorisées (Exigence 6.1).
VOTE_VALUES = st.sampled_from([-1, 0, 1])

# Séquences non vides d'opérations de vote (au moins une, jusqu'à 12 revotes).
VOTE_SEQUENCES = st.lists(VOTE_VALUES, min_size=1, max_size=12)


async def _make_fixture_pair(session: AsyncSession) -> tuple[User, Proposal]:
    """Crée un Thème, un Utilisateur et une Proposition minimalement valides.

    Retourne le couple ``(user, proposal)`` sur lequel les votes seront exercés.
    """
    theme = Theme(slug="theme-vote-prop", name="Thème (test propriété vote)")
    session.add(theme)
    await session.flush()

    user = User(
        email="voter-prop@example.test",
        password_hash="x",
        display_name="Votant",
    )
    session.add(user)
    await session.flush()

    proposal = Proposal(
        theme_id=theme.id,
        author_id=user.id,
        slug="prop-vote-prop",
        title="Proposition (test propriété vote)",
        problem="p",
        description="d",
    )
    session.add(proposal)
    await session.flush()
    return user, proposal


async def _row_count(session: AsyncSession, proposal_id: int, user_id: int) -> int:
    """Nombre de lignes ``Vote`` pour le couple (proposition, utilisateur)."""
    statement = select(func.count(Vote.id)).where(
        Vote.proposal_id == proposal_id,
        Vote.user_id == user_id,
    )
    return int((await session.execute(statement)).scalar_one())


async def _stored_value(session: AsyncSession, proposal_id: int, user_id: int) -> int | None:
    """Valeur du Vote stocké pour le couple, ou ``None`` s'il n'en existe pas."""
    statement = select(Vote.value).where(
        Vote.proposal_id == proposal_id,
        Vote.user_id == user_id,
    )
    return (await session.execute(statement)).scalar_one_or_none()


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(operations=VOTE_SEQUENCES)
async def test_vote_uniqueness_and_idempotence(
    db_session: AsyncSession, operations: list[int]
) -> None:
    """Property 1 : unicité de la ligne et valeur = dernière soumission (Exigences 6.2, 6.3)."""
    session = db_session
    # Réinitialise l'état pour cet exemple Hypothesis (le fixture est réutilisé
    # à travers les exemples au sein d'un même test).
    await session.rollback()
    await session.execute(Vote.__table__.delete())
    await session.execute(Proposal.__table__.delete())
    await session.execute(User.__table__.delete())
    await session.execute(Theme.__table__.delete())
    await session.flush()

    user, proposal = await _make_fixture_pair(session)
    service = VoteService(session)

    # Applique la séquence d'opérations de vote (revotes successives).
    counts = None
    for value in operations:
        counts = await service.cast(user, proposal.id, value)

    # 1) Au plus une (ici exactement une) ligne Vote pour le couple (Exigence 6.2/6.3).
    assert await _row_count(session, proposal.id, user.id) == 1

    # 2) La valeur stockée égale la dernière valeur soumise (Exigence 6.3).
    last_value = operations[-1]
    assert await _stored_value(session, proposal.id, user.id) == last_value

    # 3) Les décomptes renvoyés reflètent l'unique vote courant : la participation
    #    totale vaut 1, et un seul compartiment (soutien/opposition/neutre) est à 1.
    assert counts is not None
    assert counts.participation_count == 1
    assert counts.support_count == (1 if last_value == 1 else 0)
    assert counts.oppose_count == (1 if last_value == -1 else 0)
    assert counts.neutral_count == (1 if last_value == 0 else 0)


@settings(
    max_examples=200,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(first=VOTE_VALUES, second=VOTE_VALUES)
async def test_revote_replaces_without_new_row(
    db_session: AsyncSession, first: int, second: int
) -> None:
    """Une revote remplace la valeur sans créer de ligne supplémentaire (Exigence 6.3)."""
    session = db_session
    await session.rollback()
    await session.execute(Vote.__table__.delete())
    await session.execute(Proposal.__table__.delete())
    await session.execute(User.__table__.delete())
    await session.execute(Theme.__table__.delete())
    await session.flush()

    user, proposal = await _make_fixture_pair(session)
    service = VoteService(session)

    await service.cast(user, proposal.id, first)
    assert await _row_count(session, proposal.id, user.id) == 1
    assert await _stored_value(session, proposal.id, user.id) == first

    # La revote (même couple) ne crée pas de seconde ligne (Exigence 6.2/6.3).
    await service.cast(user, proposal.id, second)
    assert await _row_count(session, proposal.id, user.id) == 1
    assert await _stored_value(session, proposal.id, user.id) == second
