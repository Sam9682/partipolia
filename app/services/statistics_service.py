"""Service de statistiques globales anonymisées (Exigence 22).

Ce Service implémente le contrat ``StatisticsService`` de la conception :
``global_counts`` calcule et restitue les compteurs globaux d'Utilisateurs, de
Propositions, de Votes, de Commentaires, de Sources et de Thèmes (Exigence 22.1).

Principes directeurs :

* **Agrégation et anonymisation** (Exigence 22.2) : seules des cardinalités
  globales sont produites. Aucune requête ne groupe par ``user_id`` ni par valeur
  de Vote.
* **Non-reconstitution des opinions** (Exigence 22.3) : les Votes sont comptés en
  bloc (``COUNT(*)``) sans ventilation ; il est ainsi impossible de reconstituer
  la position d'un Utilisateur à partir de ces statistiques.

Style SQLAlchemy 2.x async : chaque compteur est un ``SELECT COUNT(*)`` agrégé,
sans chargement des lignes en mémoire. Le Service est instancié par requête avec
une ``AsyncSession``.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.proposal import Proposal
from app.models.source import Source
from app.models.theme import Theme
from app.models.user import User
from app.models.vote import Vote
from app.schemas.statistics import GlobalStatistics


class StatisticsService:
    """Calcul des statistiques globales anonymisées (Exigence 22).

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def global_counts(self) -> GlobalStatistics:
        """Retourne les compteurs globaux agrégés et anonymisés (Exigences 22.1, 22.2, 22.3).

        Chaque compteur est un ``COUNT(*)`` sur la table correspondante. Les Votes
        sont dénombrés **globalement**, sans distinction de valeur ni
        d'Utilisateur, afin de ne jamais permettre la reconstitution d'une opinion
        individuelle (Exigence 22.3).
        """
        return GlobalStatistics(
            users=await self._count(User),
            proposals=await self._count(Proposal),
            votes=await self._count(Vote),
            comments=await self._count(Comment),
            sources=await self._count(Source),
            themes=await self._count(Theme),
        )

    async def _count(self, model: type) -> int:
        """Retourne le nombre de lignes de ``model`` (``COUNT(*)`` agrégé)."""
        total = await self._session.scalar(select(func.count()).select_from(model))
        return int(total or 0)
