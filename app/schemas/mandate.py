"""Schémas Pydantic v2 pour le suivi de mandat (Exigence 21).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.mandate_service.MandateService` et de l'API du mandat
(``app/api/v1/mandate.py``) :

* :class:`CommitmentStatus` — énumération des 5 valeurs autorisées de ``status``
  ``{NOT_STARTED, IN_PROGRESS, COMPLETED, MODIFIED, ABANDONED}`` (Exigence 21.2) ;
* :class:`CommitmentPublic` — représentation publique d'un Engagement, renvoyée
  par ``GET /api/v1/mandate/commitments`` (Exigence 21.1) ;
* :class:`CommitmentInput` — corps d'``upsert`` d'un Engagement (Exigences 21.1, 21.2) ;
* :class:`IndicatorPublic` — représentation publique d'un Indicateur, renvoyée
  par ``GET /api/v1/mandate/indicators`` (Exigences 21.3, 21.4) ;
* :class:`IndicatorValueUpdate` — corps de mise à jour de ``current_value``
  (Exigence 21.4).
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class CommitmentStatus(str, Enum):
    """Valeurs autorisées de ``status`` d'un Engagement (Exigence 21.2)."""

    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    MODIFIED = "MODIFIED"
    ABANDONED = "ABANDONED"


class CommitmentPublic(BaseModel):
    """Représentation publique d'un Engagement (Exigence 21.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: str
    target_date: date | None = None
    progress: float | None = None
    notes: str | None = None
    created_at: datetime
    updated_at: datetime


class CommitmentInput(BaseModel):
    """Corps d'``upsert`` d'un Engagement (Exigences 21.1, 21.2).

    ``id`` absent ⇒ création ; ``id`` présent ⇒ mise à jour. Le ``status`` est
    contraint aux 5 valeurs autorisées (Exigence 21.2) : une valeur hors
    énumération entraîne une erreur de validation Pydantic (``422``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    id: int | None = Field(default=None, gt=0)
    title: str = Field(min_length=1, max_length=255)
    status: CommitmentStatus = CommitmentStatus.NOT_STARTED
    target_date: date | None = None
    progress: float | None = Field(default=None, ge=0.0, le=100.0)
    notes: str | None = None


class IndicatorPublic(BaseModel):
    """Représentation publique d'un Indicateur (Exigences 21.3, 21.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    unit: str | None = None
    baseline: float | None = None
    target: float | None = None
    current_value: float | None = None
    source_id: int | None = None
    created_at: datetime
    updated_at: datetime


class IndicatorValueUpdate(BaseModel):
    """Corps de mise à jour de la ``current_value`` d'un Indicateur (Exigence 21.4)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    current_value: float
