"""API REST des statistiques globales anonymisées (Exigence 22).

Point d'accès **public** (consultation sans authentification) :

* ``GET /api/v1/statistics`` — compteurs globaux d'Utilisateurs, de Propositions,
  de Votes, de Commentaires, de Sources et de Thèmes (Exigence 22.1).

Les statistiques sont **agrégées et anonymisées** (Exigence 22.2) et n'exposent
aucune donnée individuelle permettant de reconstituer les opinions d'un
Utilisateur (Exigence 22.3). Le routeur délègue tout le calcul au
:class:`~app.services.statistics_service.StatisticsService`.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.schemas.statistics import GlobalStatistics
from app.services.statistics_service import StatisticsService

router = APIRouter()


def get_statistics_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatisticsService:
    """Dépendance fournissant un :class:`StatisticsService` lié à la session de requête."""
    return StatisticsService(session)


@router.get(
    "",
    response_model=GlobalStatistics,
    summary="Compteurs globaux agrégés et anonymisés",
)
async def get_statistics(
    service: Annotated[StatisticsService, Depends(get_statistics_service)],
) -> GlobalStatistics:
    """Retourne les statistiques globales anonymisées de la Plateforme (Exigence 22.1)."""
    return await service.global_counts()
