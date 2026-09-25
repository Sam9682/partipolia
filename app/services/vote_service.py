"""Service de vote (Exigence 6).

Ce Service implémente le contrat ``VoteService`` de la conception :

* ``cast`` — enregistre le Vote d'un Utilisateur sur une Proposition via un
  **UPSERT** sur la contrainte ``UNIQUE(proposal_id, user_id)`` : un nouveau vote
  est inséré, un vote existant voit sa ``value`` remplacée sans créer de ligne
  supplémentaire (Exigences 6.1, 6.2, 6.3) ;
* ``withdraw`` — supprime (DELETE) le Vote de l'Utilisateur sur la Proposition,
  l'excluant des décomptes (Exigence 6.4) ;
* ``counts`` — renvoie les décomptes agrégés d'une Proposition (Exigence 6.5).

Les trois méthodes renvoient un :class:`VoteCounts` reflétant l'état courant des
décomptes après l'opération. La valeur du vote est bornée à ``{+1, 0, -1}`` côté
application (en plus de la contrainte CHECK ``ck_votes_value`` en base).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.models.vote import Vote
from app.schemas.vote import VoteCounts

# Valeurs de vote autorisées : soutien (+1), neutre (0), opposition (-1).
_ALLOWED_VALUES: Final[frozenset[int]] = frozenset({-1, 0, 1})

# Message d'erreur pour une valeur de vote hors du domaine autorisé (Exigence 6.1).
INVALID_VOTE_VALUE_ERROR: Final = "La valeur du vote doit être +1, 0 ou -1."


class VoteError(Exception):
    """Erreur de vote (valeur invalide, etc.)."""


class VoteService:
    """Logique de vote : UPSERT, retrait et décomptes (Exigence 6).

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Enregistrement / mise à jour d'un vote (Exigences 6.1, 6.2, 6.3)   #
    # ------------------------------------------------------------------ #
    async def cast(self, user: User, proposal_id: int, value: int) -> VoteCounts:
        """Enregistre ou met à jour le Vote d'un Utilisateur (UPSERT).

        Un UPSERT sur la contrainte ``UNIQUE(proposal_id, user_id)`` garantit
        qu'un Utilisateur ne porte qu'un seul Vote par Proposition : un nouveau
        vote est inséré, un vote déjà présent voit sa ``value`` remplacée sans
        créer de ligne supplémentaire (Exigences 6.1, 6.2, 6.3).
        """
        if value not in _ALLOWED_VALUES:
            raise VoteError(INVALID_VOTE_VALUE_ERROR)

        statement = (
            pg_insert(Vote)
            .values(proposal_id=proposal_id, user_id=user.id, value=value)
            .on_conflict_do_update(
                constraint="uq_votes_proposal_user",
                set_={"value": value},
            )
        )
        await self._session.execute(statement)
        await self._session.flush()
        return await self.counts(proposal_id)

    # ------------------------------------------------------------------ #
    # Retrait d'un vote (Exigence 6.4)                                   #
    # ------------------------------------------------------------------ #
    async def withdraw(self, user: User, proposal_id: int) -> VoteCounts:
        """Supprime le Vote de l'Utilisateur sur la Proposition (Exigence 6.4).

        Le Vote retiré est exclu des décomptes. L'opération est idempotente :
        retirer un Vote inexistant ne provoque pas d'erreur.
        """
        vote = await self._session.scalar(
            select(Vote).where(
                Vote.proposal_id == proposal_id,
                Vote.user_id == user.id,
            )
        )
        if vote is not None:
            await self._session.delete(vote)
            await self._session.flush()
        return await self.counts(proposal_id)

    # ------------------------------------------------------------------ #
    # Décomptes agrégés (Exigence 6.5, 7.1)                              #
    # ------------------------------------------------------------------ #
    async def counts(self, proposal_id: int) -> VoteCounts:
        """Retourne les décomptes agrégés des Votes d'une Proposition (Exigence 6.5).

        Une unique requête agrégée calcule les décomptes de soutien (``+1``),
        d'opposition (``-1``), de neutralité (``0``) et de participation totale.
        """
        support = func.count(case((Vote.value == 1, 1)))
        oppose = func.count(case((Vote.value == -1, 1)))
        neutral = func.count(case((Vote.value == 0, 1)))
        total = func.count(Vote.id)

        statement = select(support, oppose, neutral, total).where(
            Vote.proposal_id == proposal_id
        )
        row = (await self._session.execute(statement)).one()

        support_count, oppose_count, neutral_count, participation_count = row
        return VoteCounts(
            proposal_id=proposal_id,
            support_count=support_count,
            oppose_count=oppose_count,
            neutral_count=neutral_count,
            participation_count=participation_count,
        )
