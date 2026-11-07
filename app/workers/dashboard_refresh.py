"""Celery task refreshing the national aggregate (daily snapshot)."""

from __future__ import annotations

import asyncio
from typing import Any

from app.workers.celery_app import celery_app

_RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "max_retries": 5,
}


async def _run() -> dict[str, Any]:
    from app.core.database import SessionLocal
    from app.services.aggregation_service import AggregationService
    from app.services.simulation_service import DatabaseSnapshotProvider

    async with SessionLocal() as session:
        snapshot = await DatabaseSnapshotProvider(session).get_snapshot()
        row = await AggregationService(session).refresh(snapshot)
        await session.commit()
        return {
            "participants": row.participant_count,
            "active_commitments": row.active_commitments,
            "model_version": row.model_version,
        }


@celery_app.task(  # type: ignore[untyped-decorator]  # Celery decorator is untyped
    name="app.workers.dashboard_refresh.refresh_dashboard", **_RETRY_KWARGS
)
def refresh_dashboard() -> dict[str, Any]:
    """Recompute today's national snapshot (aggregates only, no individual data)."""
    return asyncio.run(_run())


__all__ = ["refresh_dashboard"]
