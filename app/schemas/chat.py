"""Schémas Pydantic v2 de l'Assistant IA documentaire (Exigences 13, 14, 15).

La réponse de l'Assistant est volontairement **factuelle et sourcée** : chaque
réponse expose le texte généré, la liste des Sources citées (citations
numérotées) et un indice de confiance, afin qu'aucune affirmation ne soit
présentée sans son ancrage documentaire (Exigences 13.1, 13.4).

* :class:`ChatSource` — une Source citée, dérivée d'une
  :class:`~app.rag.types.Citation` retenue pour la réponse ;
* :class:`ChatResponse` — résultat du
  :class:`~app.rag.pipeline.RagPipeline` : ``{answer, sources[], confidence}``
  (Exigence 13.1).

Lorsque le contexte documentaire est insuffisant, ``answer`` indique
explicitement l'indisponibilité, ``sources`` est vide et ``confidence`` vaut
``0.0`` (Exigences 13.8, 14.6).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    """Corps de ``POST /api/v1/chat`` : ``{message, proposal_id}`` (Exigence 13.1).

    ``message`` porte la question de l'Utilisateur (obligatoire, non vide) ;
    ``proposal_id`` rattache éventuellement la question à une Proposition afin de
    contextualiser la classification et la réécriture de requête (Exigences 13.1,
    13.2).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    message: str = Field(
        min_length=1,
        max_length=4000,
        description="Question adressée à l'Assistant IA documentaire.",
    )
    proposal_id: int | None = Field(
        default=None,
        gt=0,
        description="Proposition à laquelle rattacher la question (facultatif).",
    )


class ChatSource(BaseModel):
    """Source citée dans une réponse de l'Assistant IA (Exigence 13.4).

    Reprend les informations d'une citation numérotée afin d'afficher à
    l'Utilisateur d'où provient chaque affirmation factuelle.
    """

    number: int = Field(ge=1, description="Numéro de citation [n] affiché dans la réponse.")
    chunk_id: int = Field(description="Fragment (document_chunks) source de la citation.")
    content: str = Field(description="Texte du passage cité.")
    source_id: int | None = Field(
        default=None, description="Identifiant de la Source d'origine (métadonnée)."
    )
    document_id: int | None = Field(
        default=None, description="Identifiant du Document d'origine (métadonnée)."
    )


class ChatResponse(BaseModel):
    """Réponse de l'Assistant IA : ``{answer, sources[], confidence}`` (Exigence 13.1).

    ``answer`` est généré **uniquement** à partir du contexte documentaire
    récupéré (Exigence 13.5). ``sources`` liste les citations numérotées
    correspondantes (Exigence 13.4). ``confidence`` ∈ ``[0, 1]`` reflète l'ancrage
    documentaire : ``0.0`` lorsque aucun passage n'a été récupéré (information
    indisponible, Exigences 13.8, 14.6).
    """

    answer: str = Field(description="Réponse générée à partir du contexte documentaire.")
    sources: list[ChatSource] = Field(
        default_factory=list,
        description="Citations numérotées affichées comme Sources (Exigence 13.4).",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description=(
            "Indice de confiance dans l'ancrage documentaire de la réponse "
            "(0.0 si aucun passage récupéré)."
        ),
    )


__all__ = ["ChatRequest", "ChatSource", "ChatResponse"]
