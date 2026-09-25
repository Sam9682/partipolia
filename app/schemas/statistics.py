"""Schémas Pydantic v2 des statistiques globales anonymisées (Exigence 22).

La restitution est volontairement **agrégée et anonymisée** : seuls des compteurs
globaux sont exposés (Exigence 22.2). Aucune ventilation individuelle susceptible
de reconstituer les opinions politiques d'un Utilisateur n'est produite
(Exigence 22.3) — en particulier, les Votes ne sont **jamais** décomposés par
valeur, par Utilisateur ni par Proposition dans ce modèle.

* :class:`GlobalStatistics` — compteurs globaux exposés par
  ``GET /api/v1/statistics`` (Exigence 22.1) et calculés par
  :class:`~app.services.statistics_service.StatisticsService`.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class GlobalStatistics(BaseModel):
    """Compteurs globaux anonymisés de la Plateforme (Exigences 22.1, 22.2).

    Chaque champ est un simple décompte agrégé, jamais associé à un Utilisateur
    ni à une opinion. L'ensemble constitue la mesure de l'ampleur de la
    participation exposée aux visiteurs (Exigence 22).
    """

    users: int = Field(ge=0, description="Nombre total de comptes Utilisateurs.")
    proposals: int = Field(ge=0, description="Nombre total de Propositions.")
    votes: int = Field(
        ge=0,
        description=(
            "Nombre total de Votes exprimés, toutes valeurs confondues et sans "
            "ventilation par valeur ni par Utilisateur (Exigence 22.3)."
        ),
    )
    comments: int = Field(ge=0, description="Nombre total de Commentaires.")
    sources: int = Field(ge=0, description="Nombre total de Sources documentaires.")
    themes: int = Field(ge=0, description="Nombre total de Thèmes du Référentiel_Thématique.")
