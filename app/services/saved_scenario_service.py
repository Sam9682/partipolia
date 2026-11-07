"""Saved national scenarios and read-only share links.

Rules:
- a citizen owns at most :data:`MAX_SAVED_SCENARIOS` scenarios; the quota is checked
  under a row lock on the user (``SELECT … FOR UPDATE``) so concurrent saves cannot
  create a 51st one (Requirement 2.14);
- a scenario that does not exist or belongs to someone else raises the same
  :class:`ScenarioNotFoundError` (=> identical 404, Requirement 2.8);
- ``get`` recomputes the result with the *stored* model version (reproducibility,
  Requirement 2.5); an unavailable version raises :class:`ServiceUnavailableError` (503);
- only the SHA-256 of the share token is stored; the shared view carries no identity
  (Requirements 2.9, 2.10).
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.economics.defaults import bootstrap_snapshot
from app.economics.national import NationalScenario as EngineScenario
from app.economics.national import with_population_fallback
from app.models.national_scenario import NationalScenario, ScenarioShareLink
from app.models.user import User
from app.schemas.national_simulation import NationalScenarioResult
from app.services.economic_data_service import EconomicDataService
from app.services.national_simulation_service import (
    NationalSimulationService,
    ServiceUnavailableError,
)
from app.services.simulation_service import DatabaseSnapshotProvider, SnapshotProvider

logger = get_logger(__name__)

MAX_SAVED_SCENARIOS = 50
MIN_NAME_LENGTH = 1
MAX_NAME_LENGTH = 100
SHARE_TOKEN_BYTES = 32


class SavedScenarioError(Exception):
    """Base class of expected, user-facing saved scenario errors."""


class ScenarioNotFoundError(SavedScenarioError, LookupError):
    """Missing or foreign scenario / revoked or unknown share token (=> 404)."""


class ScenarioValidationError(SavedScenarioError, ValueError):
    """Invalid name or quota reached (=> 422)."""


@dataclass(frozen=True, slots=True)
class SavedScenarioSummary:
    id: int
    name: str
    created_at: datetime
    net_impact_central: Decimal
    model_version: str


@dataclass(frozen=True, slots=True)
class SavedScenario(SavedScenarioSummary):
    pass


@dataclass(frozen=True, slots=True)
class ShareLink:
    """The raw token is only returned once, at creation time."""

    scenario_id: int
    token: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _check_name(name: str) -> str:
    if not isinstance(name, str) or not MIN_NAME_LENGTH <= len(name) <= MAX_NAME_LENGTH:
        raise ScenarioValidationError("name: 1 à 100 caractères")
    return name


def _serialize(scenario: EngineScenario) -> dict[str, object]:
    return {
        "participation_rate": str(scenario.participation_rate),
        "reductions": {code: str(value) for code, value in scenario.reductions.items()},
        "horizon": scenario.horizon,
    }


def _deserialize(parameters: dict[str, object]) -> EngineScenario:
    reductions = parameters["reductions"]
    assert isinstance(reductions, dict)
    return EngineScenario(
        participation_rate=Decimal(str(parameters["participation_rate"])),
        reductions={code: Decimal(str(v)) for code, v in reductions.items()},
        horizon=parameters.get("horizon", "medium"),  # type: ignore[arg-type]
    )


def _summary(row: NationalScenario) -> SavedScenarioSummary:
    return SavedScenarioSummary(
        id=row.id,
        name=row.name,
        created_at=row.created_at,
        net_impact_central=row.net_impact_central,
        model_version=row.model_version,
    )


class SavedScenarioService:
    def __init__(self, session: AsyncSession, provider: SnapshotProvider | None = None) -> None:
        self._session = session
        self._provider: SnapshotProvider = provider or DatabaseSnapshotProvider(session)

    # ------------------------------------------------------------------ #
    async def save(self, user_id: int, name: str, scenario: EngineScenario) -> SavedScenario:
        _check_name(name)
        service = NationalSimulationService(self._provider, self._session)
        result = await service.calculate(scenario)  # 503 before anything is written
        version_id = getattr(self._provider, "model_version_id", None)

        # Row lock on the user serialises concurrent saves of the same citizen.
        locked = (
            await self._session.execute(select(User.id).where(User.id == user_id).with_for_update())
        ).scalar_one_or_none()
        if locked is None:
            raise ScenarioNotFoundError("user")
        count = (
            await self._session.execute(
                select(func.count())
                .select_from(NationalScenario)
                .where(NationalScenario.user_id == user_id)
            )
        ).scalar_one()
        if count >= MAX_SAVED_SCENARIOS:
            raise ScenarioValidationError("Limite de 50 scénarios enregistrés atteinte")

        row = NationalScenario(
            user_id=user_id,
            name=name,
            parameters=_serialize(scenario),
            model_version_id=version_id,
            model_version=result.model_version,
            dataset_version=", ".join(result.dataset_versions),
            net_impact_central=result.fiscal.net_public_impact.central,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return SavedScenario(
            id=row.id,
            name=row.name,
            created_at=row.created_at,
            net_impact_central=row.net_impact_central,
            model_version=row.model_version,
        )

    async def list(self, user_id: int) -> list[SavedScenarioSummary]:
        rows = (
            await self._session.execute(
                select(NationalScenario)
                .where(NationalScenario.user_id == user_id)
                .order_by(NationalScenario.created_at.desc(), NationalScenario.id.desc())
            )
        ).scalars()
        return [_summary(row) for row in rows]

    async def get(self, user_id: int, scenario_id: int) -> NationalScenarioResult:
        row = await self._owned(user_id, scenario_id)
        return await self._recompute(row)

    async def rename(self, user_id: int, scenario_id: int, name: str) -> None:
        row = await self._owned(user_id, scenario_id)
        row.name = _check_name(name)  # invalid name: raised before any change
        await self._session.flush()

    async def delete(self, user_id: int, scenario_id: int) -> None:
        row = await self._owned(user_id, scenario_id)
        await self._session.delete(row)  # share link removed by ON DELETE CASCADE
        await self._session.flush()

    async def share(self, user_id: int, scenario_id: int) -> ShareLink:
        row = await self._owned(user_id, scenario_id)
        token = secrets.token_urlsafe(SHARE_TOKEN_BYTES)
        link = (
            await self._session.execute(
                select(ScenarioShareLink).where(ScenarioShareLink.scenario_id == row.id)
            )
        ).scalar_one_or_none()
        if link is None:
            self._session.add(ScenarioShareLink(scenario_id=row.id, token_hash=hash_token(token)))
        else:  # a new token replaces (and so invalidates) the previous one
            link.token_hash = hash_token(token)
        await self._session.flush()
        return ShareLink(scenario_id=row.id, token=token)

    async def revoke_share(self, user_id: int, scenario_id: int) -> None:
        row = await self._owned(user_id, scenario_id)
        await self._session.execute(
            delete(ScenarioShareLink).where(ScenarioShareLink.scenario_id == row.id)
        )
        await self._session.flush()

    async def get_shared(self, token: str) -> NationalScenarioResult:
        if not token:
            raise ScenarioNotFoundError("share")
        row = (
            await self._session.execute(
                select(NationalScenario)
                .join(ScenarioShareLink, ScenarioShareLink.scenario_id == NationalScenario.id)
                .where(ScenarioShareLink.token_hash == hash_token(token))
            )
        ).scalar_one_or_none()
        if row is None:
            raise ScenarioNotFoundError("share")
        return await self._recompute(row)

    # ------------------------------------------------------------------ #
    async def _owned(self, user_id: int, scenario_id: int) -> NationalScenario:
        row = (
            await self._session.execute(
                select(NationalScenario).where(
                    NationalScenario.id == scenario_id, NationalScenario.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise ScenarioNotFoundError(str(scenario_id))
        return row

    async def _recompute(self, row: NationalScenario) -> NationalScenarioResult:
        """Recompute with the stored model version (bootstrap when it was NULL)."""
        try:
            if row.model_version_id is None:
                snapshot = bootstrap_snapshot()
            else:
                snapshot = await EconomicDataService(self._session).get_snapshot(
                    row.model_version_id
                )
            snapshot = with_population_fallback(snapshot, bootstrap_snapshot())
            return NationalSimulationService.calculate_with_snapshot(
                snapshot, _deserialize(row.parameters)
            )
        except Exception as exc:  # noqa: BLE001 - version/dataset missing => 503
            logger.warning("saved_scenario_recompute_failed", scenario_id=row.id, exc_info=True)
            raise ServiceUnavailableError("stored model version unavailable") from exc


__all__ = [
    "MAX_SAVED_SCENARIOS",
    "SavedScenario",
    "SavedScenarioError",
    "SavedScenarioService",
    "SavedScenarioSummary",
    "ScenarioNotFoundError",
    "ScenarioValidationError",
    "ShareLink",
    "hash_token",
]
