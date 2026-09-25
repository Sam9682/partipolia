"""Service du Référentiel_Thématique (Exigence 2).

Ce Service implémente le contrat ``ThemeService`` de la conception :

* ``list_themes`` — liste des 25 Thèmes du Référentiel_Thématique (Exigence 2.2) ;
* ``get_theme`` — détail d'un Thème par son identifiant (Exigence 2.3) ;
* ``list_proposals_for_theme`` — Propositions rattachées à un Thème, paginées
  (Exigence 2.4).

Le référentiel est **figé à 25 Thèmes** amorcés par ``scripts/seed.py`` : ce
Service est en lecture seule et ne crée ni ne modifie de Thème. Il est instancié
par requête avec une ``AsyncSession`` (SQLAlchemy 2.x async).

Un Thème inexistant est signalé par :class:`ThemeNotFoundError`, que la couche API
traduit en ``404 NOT_FOUND`` (Exigences 2.3, 2.4, 31.2).
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposal import Proposal
from app.models.theme import Theme


class ThemeNotFoundError(Exception):
    """Le Thème demandé n'existe pas dans le Référentiel_Thématique (Exigences 2.3, 2.4)."""


class ThemeService:
    """Accès en lecture au Référentiel_Thématique et à ses Propositions (Exigence 2)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Liste des Thèmes (Exigence 2.2)                                     #
    # ------------------------------------------------------------------ #
    async def list_themes(self) -> list[Theme]:
        """Retourne l'ensemble des Thèmes, ordonnés par identifiant (Exigence 2.2).

        L'ordre par ``id`` reflète l'ordre d'amorçage du Référentiel_Thématique
        (``scripts/seed.py``), c'est-à-dire l'ordre des slugs figés par l'Exigence 2.
        """
        result = await self._session.scalars(select(Theme).order_by(Theme.id))
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Détail d'un Thème (Exigence 2.3)                                    #
    # ------------------------------------------------------------------ #
    async def get_theme(self, theme_id: int) -> Theme:
        """Retourne le Thème d'identifiant ``theme_id`` (Exigence 2.3).

        Lève :class:`ThemeNotFoundError` si aucun Thème ne correspond, afin que la
        couche API renvoie ``404 NOT_FOUND``.
        """
        theme = await self._session.get(Theme, theme_id)
        if theme is None:
            raise ThemeNotFoundError(str(theme_id))
        return theme

    # ------------------------------------------------------------------ #
    # Propositions d'un Thème (Exigence 2.4)                              #
    # ------------------------------------------------------------------ #
    async def list_proposals_for_theme(
        self, theme_id: int, *, page: int, limit: int
    ) -> tuple[list[Proposal], int]:
        """Retourne les Propositions rattachées à un Thème, paginées (Exigence 2.4).

        Vérifie d'abord l'existence du Thème (:class:`ThemeNotFoundError` sinon), puis
        renvoie le couple ``(propositions_de_la_page, total)``. Les Propositions sont
        ordonnées par date de création décroissante puis par identifiant décroissant
        pour un ordre déterministe ; le tri n'utilise jamais un décompte de votes
        (Exigence 7.3).
        """
        # L'existence du Thème conditionne un 404 même lorsqu'il n'a aucune Proposition.
        await self.get_theme(theme_id)

        total = await self._session.scalar(
            select(func.count())
            .select_from(Proposal)
            .where(Proposal.theme_id == theme_id)
        )
        total_count = int(total or 0)

        offset = (page - 1) * limit
        result = await self._session.scalars(
            select(Proposal)
            .where(Proposal.theme_id == theme_id)
            .order_by(Proposal.created_at.desc(), Proposal.id.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(result.all()), total_count
