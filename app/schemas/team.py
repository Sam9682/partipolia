"""Schémas Pydantic v2 pour les Équipes (Exigence 20).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.team_service.TeamService` et de l'API des Équipes
(``app/api/v1/teams.py``) :

* :class:`TeamMemberPublic` — représentation publique d'un membre d'Équipe
  (``role``, ``bio``, ``user_id``) (Exigence 20.2) ;
* :class:`TeamPublic` — représentation publique d'une Équipe et de ses membres,
  renvoyée par ``GET /api/v1/teams`` (Exigences 20.1, 20.2) ;
* :class:`TeamCreate` — corps de création d'une Équipe (Exigence 20.3) ;
* :class:`TeamMemberCreate` — corps d'ajout d'un membre à une Équipe (role, bio)
  (Exigences 20.2, 20.3).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TeamMemberPublic(BaseModel):
    """Représentation publique d'un membre d'Équipe (Exigence 20.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    team_id: int
    user_id: int | None = None  # Exigence 5.2
    display_name: str | None = None  # Exigence 5.1
    photo_path: str | None = None  # Exigence 5.1
    linkedin_url: str | None = None  # Exigence 5.1
    role: str | None = None
    bio: str | None = None
    created_at: datetime
    updated_at: datetime


class TeamPublic(BaseModel):
    """Représentation publique d'une Équipe et de ses membres (Exigences 20.1, 20.2)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    created_at: datetime
    updated_at: datetime
    members: list[TeamMemberPublic] = Field(default_factory=list)


class TeamCreate(BaseModel):
    """Corps de création d'une Équipe (Exigence 20.3)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=255)


class TeamMemberCreate(BaseModel):
    """Corps d'ajout d'un membre à une Équipe (role, bio) (Exigences 20.2, 20.3)."""

    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: int | None = Field(default=None, gt=0)
    display_name: str | None = Field(default=None, max_length=255)
    photo_path: str | None = Field(default=None, max_length=512)
    linkedin_url: str | None = Field(default=None, max_length=512)
    role: str | None = Field(default=None, max_length=120)
    bio: str | None = None
