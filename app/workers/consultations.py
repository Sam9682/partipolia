"""Tâche Celery de clôture automatique des consultations échues (Exigence 9.9).

Planifiée toutes les 30 s (≤ 60 s) via le ``beat_schedule`` de ``celery_app``.
Idempotente : seules les consultations ``published`` dont ``closes_at`` est
passée sont clôturées ; un second passage ne trouve rien à faire. Chaque
clôture est auditée avec le système comme acteur (``actor_id = NULL``).
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.workers.celery_app import celery_app

_RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 60,
    "retry_jitter": True,
    "max_retries": 3,
}


async def _run() -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.consultation_service import ConsultationService

    async with SessionLocal() as session:
        closed = await ConsultationService(session).close_expired()
        await session.commit()
    return {"closed": closed}


@celery_app.task(  # type: ignore[untyped-decorator]  # Celery decorator is untyped
    name="app.workers.consultations.close_expired_consultations", **_RETRY_KWARGS
)
def close_expired_consultations() -> dict[str, Any]:
    """Clôture les consultations échues (acteur système, idempotent)."""
    return asyncio.run(_run())


__all__ = ["close_expired_consultations"]
