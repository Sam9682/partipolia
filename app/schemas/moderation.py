"""Schémas Pydantic v2 de la modération simple des Commentaires (Exigence 17).

La modération V1 est **simple** et **auditable** : à la soumission d'un
Commentaire, le Moteur_De_Modération applique un filtre automatique puis une
classification binaire ``conforme`` / ``douteux`` (Exigence 17.1). La décision
détermine le statut publié du Commentaire :

* ``conforme`` ⇒ Commentaire publié avec le statut ``VISIBLE`` (Exigence 17.2) ;
* ``douteux`` ⇒ Commentaire placé dans la File_De_Modération avec le statut
  ``PENDING``, sans publication (Exigence 17.3).

Chaque décision est consignée dans le Journal_D_Audit (Exigence 17.4). Le présent
module ne décrit **que** le résultat de la classification restitué par le
:class:`~app.services.moderation_service.ModerationService`.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class ModerationClassification(str, Enum):
    """Résultat de la classification automatique d'un Commentaire (Exigence 17.1).

    * :attr:`CONFORME` — le Commentaire passe le filtre : il est publié
      (``VISIBLE``, Exigence 17.2) ;
    * :attr:`DOUTEUX` — le Commentaire est signalé : il est mis en
      File_De_Modération (``PENDING``, Exigence 17.3) sans publication.
    """

    CONFORME = "conforme"
    DOUTEUX = "douteux"


class ModerationDecision(BaseModel):
    """Décision de modération appliquée à un Commentaire (Exigence 17).

    Restitue la ``classification`` retenue, le ``status`` résultant du Commentaire
    (``VISIBLE`` pour ``conforme``, ``PENDING`` pour ``douteux``) et un
    ``published`` explicite indiquant si le Commentaire a été publié. La
    ``reason`` documente, pour audit, le motif ayant déclenché la classification
    ``douteux`` (``None`` lorsque le Commentaire est conforme).
    """

    comment_id: int = Field(description="Identifiant du Commentaire modéré.")
    classification: ModerationClassification = Field(
        description="Résultat de la classification automatique (Exigence 17.1)."
    )
    status: str = Field(
        description=(
            "Statut résultant du Commentaire : VISIBLE si conforme (Exigence 17.2), "
            "PENDING si douteux (File_De_Modération, Exigence 17.3)."
        )
    )
    published: bool = Field(
        description=(
            "True si le Commentaire est publié (conforme, VISIBLE) ; "
            "False s'il est retenu en File_De_Modération (douteux, PENDING)."
        )
    )
    reason: str | None = Field(
        default=None,
        description=(
            "Motif du signalement pour un Commentaire douteux (terme filtré) ; "
            "None lorsque le Commentaire est conforme."
        ),
    )
