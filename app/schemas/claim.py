"""Schémas Pydantic v2 des Revendications (Exigences 6, 7 et 8).

* :class:`ClaimCreate` — création / modification d'une Revendication : titre
  5..120 et texte 20..2000 caractères **après** ``strip`` (Exigences 6.1, 6.4) ;
* :class:`CommentCreate` — commentaire de 1 à 1000 caractères, non vide après
  ``strip`` (Exigence 7.16) ;
* :class:`ReportCreate` — signalement avec l'un des 5 motifs (Exigence 8.1) ;
* :class:`ModerationAction` — motif de modération de 10 à 500 caractères après
  ``strip`` (Exigences 7.17, 8.8) ;
* :class:`ClaimPage` — page du catalogue ;
* :class:`SupportState` — état du soutien d'un citoyen sur une Revendication.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.common import Page

ReportReason = Literal["illegal", "hate_harassment", "off_topic", "duplicate", "other"]

REPORT_REASON_LABELS: dict[str, str] = {
    "illegal": "contenu illégal",
    "hate_harassment": "haine ou harcèlement",
    "off_topic": "hors sujet",
    "duplicate": "doublon",
    "other": "autre",
}


class ClaimCreate(BaseModel):
    """Corps de création d'une Revendication (Exigences 6.1, 6.4)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=5, max_length=120)
    body: str = Field(min_length=20, max_length=2000)
    category_code: str | None = Field(default=None, max_length=64)


class CommentCreate(BaseModel):
    """Corps d'un commentaire de Revendication (Exigence 7.16)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    body: str = Field(min_length=1, max_length=1000)


class ReportCreate(BaseModel):
    """Corps d'un signalement (Exigence 8.1)."""

    model_config = ConfigDict(extra="forbid")

    reason: ReportReason


class ModerationAction(BaseModel):
    """Motif d'une action de modération (Exigences 7.17, 8.8)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    reason: str = Field(min_length=10, max_length=500)


class ClaimItem(BaseModel):
    """Revendication publique telle que listée dans le catalogue."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    category_code: str | None = None
    category_label: str | None = None
    author_pseudonym: str | None = None
    support_count: int = Field(ge=0)
    supported_by_me: bool = False
    published_at: datetime | None = None


class ClaimOwnerView(BaseModel):
    """Revendication vue par son auteur (« mes revendications », Exigence 8.12).

    ``moderation_reason`` n'est renseigné que pour une Revendication masquée.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    body: str
    category_code: str | None = None
    status: str
    support_count: int = Field(ge=0)
    published_at: datetime | None = None
    created_at: datetime | None = None
    moderation_reason: str | None = None


class ClaimPage(Page[ClaimItem]):
    """Page du catalogue des Revendications."""


class SupportState(BaseModel):
    """État du soutien après un ``PUT`` / ``DELETE`` (idempotents)."""

    supported: bool
    support_count: int = Field(ge=0)


class ClaimDetail(ClaimItem):
    """Détail d'une Revendication (publique, ou vue par son auteur / la modération)."""

    status: str = "published"
    moderation_reason: str | None = None


class CommentItem(BaseModel):
    """Commentaire visible d'une Revendication."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    claim_id: int
    body: str
    author_pseudonym: str | None = None
    created_at: datetime | None = None


class ModerationItemOut(BaseModel):
    """Contenu masqué en attente de modération (file de modération)."""

    model_config = ConfigDict(from_attributes=True)

    kind: Literal["claim", "comment"]
    id: int
    claim_id: int
    excerpt: str
    report_count: int = Field(ge=0)
    hidden_by: str | None = None
    moderation_reason: str | None = None
    created_at: datetime
