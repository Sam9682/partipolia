"""Schémas Pydantic v2 de la popularité d'une Proposition (Exigence 7).

La restitution de la participation est volontairement **factuelle** : elle
expose toujours les décomptes absolus aux côtés du Taux_De_Soutien afin
qu'un pourcentage ne soit jamais présenté seul (Exigences 7.1, 7.4).

* :class:`Popularity` — résultat du :class:`~app.services.popularity_service.PopularityService`.

Le Taux_De_Soutien est défini comme ``support_count / (support_count +
oppose_count)`` et vaut ``0`` lorsque le dénominateur est nul (Exigence 7.2) ;
les Votes NEUTRE (``value == 0``) sont exclus du dénominateur mais comptés dans
``participation_count``.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Popularity(BaseModel):
    """Popularité restituée d'une Proposition (Exigences 7.1, 7.2, 7.4).

    Les décomptes absolus (``support_count``, ``oppose_count``,
    ``participation_count``) sont **toujours** renvoyés en même temps que
    ``support_rate`` : aucun taux n'est exposé sans son support chiffré
    (Exigence 7.4).
    """

    proposal_id: int
    support_count: int = Field(ge=0, description="Nombre de Votes de soutien (value == +1).")
    oppose_count: int = Field(ge=0, description="Nombre de Votes d'opposition (value == -1).")
    participation_count: int = Field(
        ge=0,
        description="Nombre total de Votes exprimés, NEUTRE (value == 0) inclus.",
    )
    support_rate: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Taux_De_Soutien = support_count / (support_count + oppose_count) ; "
            "0.0 lorsque le dénominateur est nul (Exigence 7.2)."
        ),
    )
