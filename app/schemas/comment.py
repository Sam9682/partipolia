"""Schémas Pydantic v2 pour les Commentaires (Exigence 9).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.comment_service.CommentService` et de l'API des
Commentaires (``app/api/v1/comments.py``) :

* :class:`CommentCreate` — corps de ``POST /api/v1/proposals/{id}/comments``
  portant le ``content`` et un éventuel ``parent_id`` (fil de discussion,
  Exigences 9.1, 9.2) ;
* :class:`CommentUpdate` — corps de ``PUT /api/v1/comments/{id}`` : modification
  du ``content`` (Exigence 9.3) ;
* :class:`CommentPublic` — représentation publique « plate » d'un Commentaire
  (Exigences 9.3, 9.4) ;
* :class:`CommentNode` — nœud d'un fil de discussion : un Commentaire et ses
  ``replies`` (arborescence, Exigence 9.2) ;
* :class:`CommentSubmission` — résultat de la soumission d'un Commentaire :
  le Commentaire créé et la :class:`ModerationDecision` associée (Exigences 9.1,
  17.x).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.moderation import ModerationDecision


class CommentCreate(BaseModel):
    """Corps de ``POST /api/v1/proposals/{id}/comments`` (Exigences 9.1, 9.2).

    Un Commentaire porte un ``content`` non vide et peut référencer un
    Commentaire parent via ``parent_id`` pour former un fil de discussion
    (Exigence 9.2). Le Commentaire créé passe systématiquement par le
    Moteur_De_Modération (Exigences 9.1, 17.x).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)
    parent_id: int | None = Field(
        default=None,
        ge=1,
        description="Identifiant du Commentaire parent pour un fil de discussion (Exigence 9.2).",
    )


class CommentUpdate(BaseModel):
    """Corps de ``PUT /api/v1/comments/{id}`` (Exigence 9.3).

    Seul le ``content`` d'un Commentaire est modifiable ; le rattachement à la
    Proposition et au parent reste immuable.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)


class CommentPublic(BaseModel):
    """Représentation publique « plate » d'un Commentaire (Exigences 9.3, 9.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    author_id: int
    parent_id: int | None
    content: str
    status: str
    created_at: datetime


class CommentNode(BaseModel):
    """Nœud d'un fil de discussion : un Commentaire et ses réponses (Exigence 9.2).

    Les ``replies`` sont ordonnées chronologiquement (par identifiant croissant)
    et forment l'arborescence du fil restituée par
    :meth:`CommentService.list_thread`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    author_id: int
    parent_id: int | None
    content: str
    status: str
    created_at: datetime
    replies: list["CommentNode"] = Field(default_factory=list)


class CommentSubmission(BaseModel):
    """Résultat de la soumission d'un Commentaire (Exigences 9.1, 17.x).

    Restitue le ``comment`` tel que persisté (avec son ``status`` résultant de la
    modération) et la ``moderation`` :class:`ModerationDecision` décrivant l'issue
    (``VISIBLE`` si conforme, ``PENDING`` si placé en File_De_Modération).
    """

    comment: CommentPublic
    moderation: ModerationDecision
