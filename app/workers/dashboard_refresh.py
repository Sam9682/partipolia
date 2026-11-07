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
    from app.core.logging import get_logger
    from app.services.aggregation_service import AggregationService
    from app.services.regional_aggregation_service import RegionalAggregationService
    from app.services.simulation_service import DatabaseSnapshotProvider

    log = get_logger(__name__)
    result: dict[str, Any] = {}
    errors: list[BaseException] = []

    async with SessionLocal() as session:
        snapshot = await DatabaseSnapshotProvider(session).get_snapshot()

        # National first; a failure must not prevent the regional refresh.
        try:
            async with session.begin_nested():
                row = await AggregationService(session).refresh(snapshot)
            result.update(
                participants=row.participant_count,
                active_commitments=row.active_commitments,
                model_version=row.model_version,
            )
        except Exception as exc:
            log.exception("national_refresh_failed")
            errors.append(exc)

        # Regional, same snapshot and same session.
        try:
            report = await RegionalAggregationService(session).refresh(snapshot)
            result["regions_succeeded"] = len(report.succeeded)
            result["regions_failed"] = len(report.failed)
        except Exception as exc:
            log.exception("regional_refresh_task_failed")
            errors.append(exc)

        await session.commit()

    if len(errors) == 2:
        # Nothing was refreshed: surface the failure so Celery retries.
        raise errors[0]
    return result


@celery_app.task(  # type: ignore[untyped-decorator]  # Celery decorator is untyped
    name="app.workers.dashboard_refresh.refresh_dashboard", **_RETRY_KWARGS
)
def refresh_dashboard() -> dict[str, Any]:
    """Recompute today's national snapshot (aggregates only, no individual data)."""
    return asyncio.run(_run())


__all__ = ["refresh_dashboard"]
