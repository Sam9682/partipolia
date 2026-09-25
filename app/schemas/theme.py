"""Schémas Pydantic v2 pour le Référentiel_Thématique (Exigence 2).

Ces schémas définissent les contrats de sortie de l'API Thèmes
(``app/api/v1/themes.py``) et du :class:`~app.services.theme_service.ThemeService` :

* :class:`ThemePublic` — représentation publique d'un Thème (Exigences 2.2, 2.3) ;
* :class:`ProposalSummary` — résumé d'une Proposition rattachée à un Thème,
  renvoyé par ``GET /api/v1/themes/{id}/proposals`` (Exigence 2.4).

La liste des Propositions d'un Thème est paginée via :class:`~app.schemas.common.Page`.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ThemePublic(BaseModel):
    """Représentation publique d'un Thème (Exigences 2.2, 2.3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    name: str


class ProposalSummary(BaseModel):
    """Résumé d'une Proposition rattachée à un Thème (Exigence 2.4).

    Vue allégée destinée aux listes : elle n'expose pas les champs longs (problème,
    description, financement, …) réservés au détail d'une Proposition.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    theme_id: int
    author_id: int
    slug: str
    title: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
