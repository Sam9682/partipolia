"""Schémas Pydantic v2 de la détection de doublons à la création (Exigence 5).

Lorsqu'un Contributeur crée une Proposition, la Plateforme calcule un embedding
de la Proposition et recherche les Propositions proches par similarité cosinus
(Exigence 5.1). Si au moins une Proposition proche est trouvée, la réponse de
création expose la liste des :class:`SimilarProposal` accompagnée du message
« Des propositions similaires existent » et des trois actions possibles —
Consulter, Créer quand même, Améliorer une proposition existante (Exigence 5.2).
Le choix « Créer quand même » ne bloque **jamais** la création : la Proposition
est renvoyée dans tous les cas (Exigence 5.3).

* :class:`SimilarProposal` — une Proposition existante jugée proche, avec son
  score de similarité ∈ ``[0, 1]`` ;
* :class:`DuplicateActions` — libellés des trois actions proposées (Exigence 5.2) ;
* :class:`ProposalWithDuplicates` — enveloppe renvoyée par ``POST /proposals`` :
  la Proposition créée + les éventuels doublons + le message + les actions.
"""

from __future__ import annotations

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.proposal import ProposalDetail

# Message affiché lorsqu'au moins une Proposition proche est trouvée (Exigence 5.2).
SIMILAR_PROPOSALS_MESSAGE: Final = "Des propositions similaires existent"

# Libellés par défaut des trois actions offertes (Exigence 5.2).
ACTION_VIEW: Final = "Consulter"
ACTION_CREATE_ANYWAY: Final = "Créer quand même"
ACTION_IMPROVE: Final = "Améliorer une proposition existante"


class SimilarProposal(BaseModel):
    """Proposition existante jugée proche du brouillon en cours (Exigence 5.1).

    Le ``similarity`` est la similarité cosinus normalisée dans ``[0, 1]`` (1 =
    quasi-identique) entre l'embedding de la nouvelle Proposition et celui de la
    Proposition existante ; ``id``, ``slug`` et ``title`` permettent l'action
    « Consulter » (Exigence 5.2).
    """

    id: int = Field(gt=0, description="Identifiant de la Proposition proche.")
    slug: str = Field(description="Slug de la Proposition proche (lien Consulter).")
    title: str = Field(description="Titre de la Proposition proche.")
    similarity: float = Field(
        ge=0.0,
        le=1.0,
        description="Similarité cosinus ∈ [0, 1] avec la Proposition en cours.",
    )


class DuplicateActions(BaseModel):
    """Actions offertes en présence de doublons potentiels (Exigence 5.2).

    Les trois actions sont **non bloquantes** : « Créer quand même » confirme la
    création sans empêchement (Exigence 5.3).
    """

    view: str = Field(default=ACTION_VIEW, description="Consulter une Proposition proche.")
    create_anyway: str = Field(
        default=ACTION_CREATE_ANYWAY,
        description="Créer la Proposition malgré les doublons (Exigence 5.3).",
    )
    improve: str = Field(
        default=ACTION_IMPROVE,
        description="Améliorer une Proposition existante plutôt que d'en créer une.",
    )


class ProposalWithDuplicates(BaseModel):
    """Résultat de ``POST /proposals`` avec détection de doublons (Exigence 5).

    ``proposal`` est **toujours** la Proposition créée (la création n'est jamais
    bloquée — Exigence 5.3). ``similar`` liste les Propositions proches détectées ;
    lorsqu'elle est non vide, ``message`` et ``actions`` sont renseignés afin que
    l'interface propose Consulter / Créer quand même / Améliorer (Exigence 5.2).
    """

    model_config = ConfigDict(from_attributes=True)

    proposal: ProposalDetail
    similar: list[SimilarProposal] = Field(
        default_factory=list,
        description="Propositions proches détectées (vide si aucune, Exigence 5.1).",
    )
    message: str | None = Field(
        default=None,
        description="Message « Des propositions similaires existent » si doublons.",
    )
    actions: DuplicateActions | None = Field(
        default=None,
        description="Actions Consulter / Créer quand même / Améliorer si doublons.",
    )


__all__ = [
    "SimilarProposal",
    "DuplicateActions",
    "ProposalWithDuplicates",
    "SIMILAR_PROPOSALS_MESSAGE",
    "ACTION_VIEW",
    "ACTION_CREATE_ANYWAY",
    "ACTION_IMPROVE",
]
