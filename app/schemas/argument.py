"""Schémas Pydantic v2 pour les Arguments (Exigence 8).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.argument_service.ArgumentService` et de l'API des Arguments
(``app/api/v1/arguments.py``) :

* :class:`ArgumentPosition` — énumération des positions autorisées ``{FOR,
  AGAINST}`` (Exigence 8.1) ;
* :class:`ArgumentCreate` — corps de ``POST /api/v1/proposals/{id}/arguments``
  portant la ``position`` et le ``content`` (Exigence 8.1) ;
* :class:`ArgumentUpdate` — corps de ``PUT /api/v1/arguments/{id}`` : modification
  du ``content`` (Exigence 8.2) ;
* :class:`ArgumentPublic` — représentation publique d'un Argument, renvoyée par la
  création et la modification (Exigences 8.1, 8.2).
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ArgumentPosition(str, Enum):
    """Positions autorisées d'un Argument (Exigence 8.1)."""

    FOR = "FOR"
    AGAINST = "AGAINST"


class ArgumentCreate(BaseModel):
    """Corps de ``POST /api/v1/proposals/{id}/arguments`` (Exigence 8.1).

    Un Argument prend une ``position`` parmi ``{FOR, AGAINST}`` et porte un
    ``content`` non vide. Une ``position`` hors énumération entraîne une erreur de
    validation Pydantic (``422``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    position: ArgumentPosition
    content: str = Field(min_length=1, max_length=5000)


class ArgumentUpdate(BaseModel):
    """Corps de ``PUT /api/v1/arguments/{id}`` (Exigence 8.2).

    Seul le ``content`` d'un Argument est modifiable ; la ``position`` et le
    rattachement à la Proposition restent immuables.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(min_length=1, max_length=5000)


class ArgumentPublic(BaseModel):
    """Représentation publique d'un Argument (Exigences 8.1, 8.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    proposal_id: int
    author_id: int
    position: str
    content: str
    created_at: datetime
