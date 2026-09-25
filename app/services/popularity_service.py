"""Service de popularité des Propositions (Exigence 7).

Ce Service implémente le contrat ``PopularityService`` de la conception :
``compute`` calcule et restitue, pour une Proposition, les décomptes absolus de
Votes et le Taux_De_Soutien (Exigences 7.1, 7.2, 7.4).

Règles métier :

* ``support_count`` — nombre de Votes de soutien (``value == +1``) ;
* ``oppose_count`` — nombre de Votes d'opposition (``value == -1``) ;
* ``participation_count`` — nombre total de Votes exprimés, **NEUTRE inclus**
  (``value == 0``) (Exigence 7.1) ;
* ``support_rate`` — ``support_count / (support_count + oppose_count)``, et vaut
  ``0`` lorsque ce dénominateur est nul (Exigence 7.2). Les Votes NEUTRE sont
  **exclus du dénominateur** du taux mais restent comptés dans la participation.

Les décomptes absolus sont **toujours** renvoyés aux côtés du taux, afin qu'un
pourcentage ne soit jamais présenté seul (Exigence 7.4).

Style SQLAlchemy 2.x async : une unique requête agrégée regroupe les Votes par
``value`` pour éviter le problème N+1 et le chargement des lignes en mémoire.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.vote import Vote
from app.schemas.popularity import Popularity


class PopularityService:
    """Calcul de la popularité restituée d'une Proposition (Exigence 7).

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def compute(self, proposal_id: int) -> Popularity:
        """Calcule décomptes et Taux_De_Soutien d'une Proposition (Exigences 7.1, 7.2, 7.4).

        Une seule requête agrégée regroupe les Votes de la Proposition par
        ``value`` ; les Propositions sans Vote produisent des décomptes nuls et un
        ``support_rate`` de ``0.0`` (Exigence 7.2).
        """
        result = await self._session.execute(
            select(Vote.value, func.count())
            .where(Vote.proposal_id == proposal_id)
            .group_by(Vote.value)
        )

        support_count = 0
        oppose_count = 0
        neutral_count = 0
        for value, count in result.all():
            if value == 1:
                support_count = count
            elif value == -1:
                oppose_count = count
            else:  # value == 0 : Vote NEUTRE
                neutral_count = count

        # NEUTRE inclus dans la participation (Exigence 7.1) mais exclu du
        # dénominateur du taux (Exigence 7.2).
        participation_count = support_count + oppose_count + neutral_count
        denominator = support_count + oppose_count
        support_rate = support_count / denominator if denominator > 0 else 0.0

        return Popularity(
            proposal_id=proposal_id,
            support_count=support_count,
            oppose_count=oppose_count,
            participation_count=participation_count,
            support_rate=support_rate,
        )
