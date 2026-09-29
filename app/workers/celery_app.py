"""Application Celery partagée (Exigence 23).

Ce module construit l'unique instance :class:`~celery.Celery` du monolithe
modulaire. Le broker **et** le back-end de résultats sont Redis
(``settings.redis_url``, cf. :mod:`app.core.redis`), conformément à la conception
(« Broker/back-end Redis ; l'API émet les tâches sans bloquer »).

L'API FastAPI n'importe que cette application pour *émettre* les tâches
(``ingest_document.delay(...)``) sans jamais exécuter le pipeline dans le chemin
de requête (Exigence 23.3). Le Worker Celery, lui, est démarré avec
``celery -A app.workers.celery_app:celery_app worker`` (cf. ``Dockerfile.worker``)
et découvre les tâches via ``include``.

Politique de fiabilité (Exigence 23, « Échecs des tâches Celery ») :

* ``acks_late`` — une tâche n'est acquittée qu'après exécution réussie, de sorte
  qu'un worker qui meurt en cours de route ne perd pas le travail ;
* ``task_reject_on_worker_lost`` — re-planifie une tâche dont le worker a disparu ;
* le **réessai avec backoff exponentiel** est déclaré par tâche (voir
  :mod:`app.workers.ingestion`), pour les erreurs transitoires (téléchargement,
  Provider, base) et un échec propre au-delà du nombre maximal de tentatives.
"""

from __future__ import annotations

from celery import Celery

from app.core.config import settings

# Liste des modules contenant des tâches, découverts au démarrage du worker.
_TASK_MODULES: tuple[str, ...] = (
    "app.workers.ingestion",
    "app.workers.statistics",
    "app.workers.legal_analysis",
)


def create_celery_app() -> Celery:
    """Construit et configure l'application Celery (broker/back-end Redis)."""
    app = Celery(
        "partipolia",
        broker=settings.redis_url,
        backend=settings.redis_url,
        include=list(_TASK_MODULES),
    )

    app.conf.update(
        # Sérialisation JSON uniquement (pas de pickle : sûreté).
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        # Fiabilité : acquittement tardif + re-planification si le worker meurt.
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        # Un seul message pré-récupéré par worker : lissage de charge pour des
        # tâches d'ingestion longues et inégales.
        worker_prefetch_multiplier=1,
        # Conserve les résultats une heure (suffisant pour le suivi d'ingestion).
        result_expires=3600,
    )

    return app


# Instance partagée importée par l'API (émission) et le worker (exécution).
celery_app: Celery = create_celery_app()

__all__ = ["celery_app", "create_celery_app"]
