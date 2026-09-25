"""Schémas Pydantic v2 pour les Sources documentaires (Exigence 10).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.source_service.SourceService` et de l'API des Sources
(``app/api/v1/sources.py``) :

* :class:`SourceType` — énumération des 7 valeurs autorisées de ``source_type``
  ``{OFFICIAL, ACADEMIC, STATISTICAL, MEDIA, REPORT, LEGISLATION, OTHER}``
  (Exigence 10.2) ;
* :class:`SourceCreate` — corps de ``POST /api/v1/sources`` (Exigence 10.1) ;
* :class:`SourcePublic` — représentation publique d'une Source, renvoyée par
  ``GET /api/v1/sources`` et ``POST /api/v1/sources`` (Exigences 10.1, 10.2) ;
* :class:`ProposalSourceAttach` — corps de
  ``POST /api/v1/proposals/{id}/sources`` portant le ``relevance_score``
  (Exigence 10.3) ;
* :class:`ProposalSourcePublic` — représentation de l'association
  ``proposal_sources`` créée (Exigence 10.3).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceType(str, Enum):
    """Valeurs autorisées de ``source_type`` (Exigence 10.2)."""

    OFFICIAL = "OFFICIAL"
    ACADEMIC = "ACADEMIC"
    STATISTICAL = "STATISTICAL"
    MEDIA = "MEDIA"
    REPORT = "REPORT"
    LEGISLATION = "LEGISLATION"
    OTHER = "OTHER"


class SourceCreate(BaseModel):
    """Corps de ``POST /api/v1/sources`` (Exigences 10.1, 10.2).

    Les champs ``title`` (obligatoire) et ``source_type`` (contraint aux 7
    valeurs) définissent la Source ; les autres champs sont optionnels. Une
    valeur ``source_type`` hors énumération entraîne une erreur de validation
    Pydantic (``422``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=500)
    url: str | None = Field(default=None, max_length=2048)
    publisher: str | None = Field(default=None, max_length=255)
    source_type: SourceType = SourceType.OTHER
    publication_date: date | None = None
    is_verified: bool = False


class SourcePublic(BaseModel):
    """Représentation publique d'une Source (Exigences 10.1, 10.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    url: str | None = None
    publisher: str | None = None
    source_type: str
    publication_date: date | None = None
    is_verified: bool
    created_at: datetime
    updated_at: datetime


class ProposalSourceAttach(BaseModel):
    """Corps de ``POST /api/v1/proposals/{id}/sources`` (Exigence 10.3).

    Rattache une Source existante à une Proposition en enregistrant un
    ``relevance_score`` optionnel dans l'intervalle ``[0, 1]`` (contrainte
    ``CHECK`` de ``proposal_sources``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    source_id: int = Field(gt=0)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    note: str | None = None


class ProposalSourcePublic(BaseModel):
    """Représentation de l'association ``proposal_sources`` (Exigence 10.3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    source_id: int
    relevance_score: float | None = None
    note: str | None = None
    created_at: datetime
