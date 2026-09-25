"""Tâche Celery de recalcul des statistiques globales (Exigences 22, 23).

Ce module fournit la tâche ``recalculate_statistics`` requise par l'Exigence 23.1.
Elle recalcule les compteurs globaux anonymisés (Exigence 22) hors du chemin de
requête FastAPI (Exigence 23.3), de sorte que l'agrégation — potentiellement
coûteuse à grande échelle — ne bloque jamais l'API.

Découpage (aligné sur :mod:`app.workers.ingestion`) :

* La **logique de calcul** reste dans
  :class:`~app.services.statistics_service.StatisticsService` : la tâche n'orchestre
  que l'ouverture d'une session et l'appel du Service.
* La journalisation structurée (``request_id``) est importée de façon **différée**
  et son échec est toléré, afin que le module reste importable dans un
  environnement de test hors-ligne dépourvu de la couche d'observabilité.

Le résultat retourné est un dictionnaire sérialisable JSON (compteurs globaux),
adapté au back-end de résultats Redis. Les Votes y sont dénombrés globalement,
sans ventilation par valeur ni par Utilisateur (Exigence 22.3).
"""

from __future__ import annotations

from typing import Any


class _NullLogger:
    """Logger de repli sans effet (cf. :mod:`app.workers.ingestion`)."""

    def __getattr__(self, _name: str) -> Any:
        def _noop(*_args: object, **_kwargs: object) -> None:
            return None

        return _noop


class _null_context:
    """Context manager sans effet (repli de ``bind_request_id``)."""

    def __enter__(self) -> str:
        return ""

    def __exit__(self, *_exc: object) -> bool:
        return False


def _log() -> Any:
    """Retourne le logger structuré (import différé, échec toléré)."""
    try:
        from app.core.logging import get_logger
    except Exception:  # pragma: no cover - couche logging indisponible
        return _NullLogger()
    return get_logger(__name__)


def _bind_request_id() -> Any:
    """Context manager de corrélation (import différé, échec toléré)."""
    try:
        from app.core.logging import bind_request_id
    except Exception:  # pragma: no cover - couche logging indisponible
        return _null_context()
    return bind_request_id()


async def _run_recalculate() -> dict[str, Any]:
    """Ouvre une session, calcule les compteurs globaux et les sérialise.

    Fonction ``async`` dédiée exécutée par ``asyncio.run`` depuis le corps
    synchrone de la tâche Celery. Le calcul est entièrement délégué au
    :class:`StatisticsService` (pas de logique dupliquée).
    """
    from app.core.database import SessionLocal
    from app.services.statistics_service import StatisticsService

    async with SessionLocal() as session:
        stats = await StatisticsService(session).global_counts()
        return stats.model_dump()


# Paramètres de réessai communs (Exigence 23 — backoff exponentiel), alignés sur
# les tâches d'ingestion.
_RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "max_retries": 5,
}


# Import de l'application Celery après les définitions (évite les cycles et garde
# la logique importable même sans Celery configuré).
from app.workers.celery_app import celery_app  # noqa: E402


@celery_app.task(
    name="app.workers.statistics.recalculate_statistics", **_RETRY_KWARGS
)
def recalculate_statistics() -> dict[str, Any]:
    """Recalcule et retourne les statistiques globales anonymisées (Exigences 22, 23.1).

    Exécutée par le Worker_Celery hors du chemin de requête (Exigence 23.3).
    Retourne les compteurs globaux (dictionnaire JSON) ; réessaie avec backoff
    exponentiel en cas d'erreur transitoire (base).
    """
    import asyncio

    with _bind_request_id():
        _log().info("task_recalculate_statistics_start")
        result = asyncio.run(_run_recalculate())
        _log().info("task_recalculate_statistics_completed", **result)
        return result


__all__ = ["recalculate_statistics"]
