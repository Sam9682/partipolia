"""Simulation service: glue between the pure economic engine and the API.

The service obtains an immutable :class:`ModelSnapshot` from a provider (the
published model version, or the bootstrap snapshot when none is published yet),
runs the engine through :mod:`app.services.simulation_presenter` and optionally
stores the simulation *input* (never any personal data) so that it can be re-read
and recomputed with the exact model version it used.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.economics.defaults import bootstrap_snapshot
from app.economics.types import ModelSnapshot
from app.models.consumption_category import ConsumptionCategory
from app.models.simulation import EconomicSimulation, SimulationResult
from app.schemas.simulation import SimulationRequest, SimulationResponse
from app.services.economic_data_service import EconomicDataService
from app.services.simulation_presenter import UnknownCategoryError, build_response

logger = get_logger(__name__)

__all__ = [
    "BootstrapSnapshotProvider",
    "DatabaseSnapshotProvider",
    "SimulationForbiddenError",
    "SimulationNotFoundError",
    "SimulationService",
    "UnknownCategoryError",
]


class SnapshotProvider(Protocol):
    async def get_snapshot(self) -> ModelSnapshot: ...


class BootstrapSnapshotProvider:
    """Provides the snapshot built from the datasets shipped with the package."""

    async def get_snapshot(self) -> ModelSnapshot:
        return bootstrap_snapshot()


class DatabaseSnapshotProvider:
    """Latest published model version; falls back to the bootstrap snapshot.

    The anonymous simulator must keep working even if no model version has been
    published yet or the database is momentarily unavailable. The fallback is
    logged, and the response always names the model version actually used.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self.model_version_id: int | None = None

    async def get_snapshot(self) -> ModelSnapshot:
        try:
            latest = await EconomicDataService(self._session).get_latest_published()
        except Exception:  # noqa: BLE001 - never break the public simulator
            logger.warning("snapshot_provider_fallback", exc_info=True)
            return bootstrap_snapshot()
        if latest is None:
            return bootstrap_snapshot()
        version, snapshot = latest
        self.model_version_id = version.id
        return snapshot


class SimulationNotFoundError(LookupError):
    pass


class SimulationForbiddenError(PermissionError):
    pass


class SimulationService:
    def __init__(
        self,
        provider: SnapshotProvider | None = None,
        session: AsyncSession | None = None,
    ) -> None:
        self._provider: SnapshotProvider = provider or BootstrapSnapshotProvider()
        self._session = session

    async def calculate(
        self, request: SimulationRequest, user_id: int | None = None
    ) -> SimulationResponse:
        snapshot = await self._provider.get_snapshot()
        response = build_response(snapshot, request)
        if request.save and self._session is not None:
            response.simulation_id = await self._persist(snapshot, request, response, user_id)
        return response

    async def get(self, simulation_id: int, user_id: int | None) -> SimulationResponse:
        """Re-run a stored simulation on the exact model version it used."""
        if self._session is None:
            raise SimulationNotFoundError(simulation_id)
        stored = await self._session.get(EconomicSimulation, simulation_id)
        if stored is None:
            raise SimulationNotFoundError(simulation_id)
        if stored.user_id is not None and stored.user_id != user_id:
            raise SimulationForbiddenError(simulation_id)
        if stored.model_version_id is None:
            snapshot = bootstrap_snapshot()
        else:
            snapshot = await EconomicDataService(self._session).get_snapshot(
                stored.model_version_id
            )
        payload = stored.input_payload
        request = SimulationRequest(
            reductions={k: Decimal(v) for k, v in payload["reductions"].items()},
            adults=payload["adults"],
            horizon=payload.get("horizon", "medium"),
            reference_overrides={
                k: Decimal(v) for k, v in payload.get("reference_overrides", {}).items()
            },
        )
        response = build_response(snapshot, request)
        response.simulation_id = stored.id
        return response

    @staticmethod
    def calculate_with_snapshot(
        snapshot: ModelSnapshot, request: SimulationRequest
    ) -> SimulationResponse:
        return build_response(snapshot, request)

    async def _persist(
        self,
        snapshot: ModelSnapshot,
        request: SimulationRequest,
        response: SimulationResponse,
        user_id: int | None,
    ) -> int:
        assert self._session is not None
        version_id = getattr(self._provider, "model_version_id", None)
        categories = {
            c.code: c.id
            for c in (await self._session.execute(select(ConsumptionCategory))).scalars()
        }
        simulation = EconomicSimulation(
            user_id=user_id,
            model_version_id=version_id,
            model_version=snapshot.model_version,
            dataset_version=", ".join(snapshot.dataset_versions),
            scenario_type="personal",
            input_payload={
                "reductions": {k: str(v) for k, v in request.reductions.items()},
                "adults": request.adults,
                "horizon": request.horizon,
                "reference_overrides": {
                    k: str(v) for k, v in request.reference_overrides.items()
                },
            },
        )
        zero = Decimal(0)
        simulation.results = [
            SimulationResult(
                category_id=categories[row.code],
                personal_savings=row.annual_savings,
                direct_fiscal_impact=row.direct_vat.central,
                indirect_fiscal_impact=row.indirect.central if row.indirect else zero,
                induced_fiscal_impact=row.induced.central if row.induced else zero,
                additional_public_expenditure=(
                    row.additional_public_expenditure.central
                    if row.additional_public_expenditure
                    else zero
                ),
                net_fiscal_impact=row.net_public_impact.central if row.net_public_impact else zero,
                confidence_low=row.net_public_impact.low if row.net_public_impact else zero,
                confidence_high=row.net_public_impact.high if row.net_public_impact else zero,
            )
            for row in response.categories
            if row.code in categories
        ]
        self._session.add(simulation)
        await self._session.flush()
        return simulation.id
