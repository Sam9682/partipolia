"""Aggregation of declared commitments into national impact snapshots.

Privacy rules:

* only participants who explicitly consented (``consents.aggregate_commitments``)
  and whose account is active are counted;
* only totals are produced; individual commitments are never exposed;
* the public dashboard withholds every figure below ``MIN_COHORT_SIZE``
  participants (re-identification protection).

The impact is computed once on the *aggregate* avoided spending. This is exact
because the engine is linear in the avoided spending.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, cast, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.economics.aggregate import aggregate_savings
from app.economics.decimal_utils import ZERO, money
from app.economics.engine import run_engine
from app.economics.types import ModelSnapshot, PersonalSavings
from app.models.aggregation import AggregatedImpact
from app.models.citizen_commitment import CitizenCommitment
from app.models.consumption_category import ConsumptionCategory
from app.models.net_balance_verdict import NetBalanceVerdict
from app.models.user import User
from app.schemas.dashboard import (
    DashboardHistory,
    DashboardNational,
    DashboardSectors,
    HistoryPoint,
    NetContributorIndicators,
)
from app.schemas.simulation import FiscalResult, MoneyRange
from app.services.commitment_service import CONSENT_KEY
from app.services.simulation_presenter import DISCLAIMERS, METHODOLOGY_FISCAL, build_fiscal_result
from app.services.simulation_service import SnapshotProvider

#: Minimum number of participants before any aggregate is published
#: (env ``PARTICIPANTS_MIN_FOR_IMPACT``, default 20).
MIN_COHORT_SIZE = get_settings().participants_min_for_impact
DAYS_PER_YEAR = Decimal(365)
MONTHS_PER_YEAR = Decimal(12)


@dataclass(frozen=True, slots=True)
class AggregateInputs:
    participants: int
    active_commitments: int
    avoided_by_category: dict[str, Decimal]  # EUR per year, TTC


@dataclass(frozen=True, slots=True)
class NetContributorInputs:
    contributors: int  # distinct users
    active_commitments: int
    total_reduction: Decimal  # EUR per year, TTC
    mean_reduction_per_contributor: Decimal
    mean_reduction_percentage: Decimal  # mean of percentages, per commitment


def _scale(r: MoneyRange, divisor: Decimal) -> MoneyRange:
    return MoneyRange(
        low=money(r.low / divisor), central=money(r.central / divisor), high=money(r.high / divisor)
    )


class AggregationService:
    def __init__(self, session: AsyncSession, provider: SnapshotProvider | None = None) -> None:
        self._session = session
        self._provider = provider

    # ------------------------------------------------------------------ #
    async def collect(self, today: date | None = None) -> AggregateInputs:
        today = today or date.today()
        consented = (
            User.is_active.is_(True),
            User.consents[CONSENT_KEY].as_boolean().is_(True),
        )
        current = (
            CitizenCommitment.status == "active",
            CitizenCommitment.start_date <= today,
            CitizenCommitment.end_date >= today,
            CitizenCommitment.reduction_percentage > 0,
        )
        joined = (
            select(
                ConsumptionCategory.code,
                func.sum(
                    ConsumptionCategory.reference_annual_expenditure
                    * User.household_adults
                    * cast(CitizenCommitment.reduction_percentage, Numeric(20, 8))
                    / 100
                ),
                func.count(CitizenCommitment.id),
            )
            .join(CitizenCommitment, CitizenCommitment.category_id == ConsumptionCategory.id)
            .join(User, User.id == CitizenCommitment.user_id)
            .where(*consented, *current)
            .group_by(ConsumptionCategory.code)
        )
        avoided: dict[str, Decimal] = {}
        commitments = 0
        for code, total, count in (await self._session.execute(joined)).all():
            avoided[code] = Decimal(total or 0)
            commitments += int(count)
        participants = await self._session.scalar(
            select(func.count(distinct(User.id)))
            .join(CitizenCommitment, CitizenCommitment.user_id == User.id)
            .where(*consented, *current)
        )
        return AggregateInputs(int(participants or 0), commitments, avoided)

    async def collect_net_contributors(self, today: date | None = None) -> NetContributorInputs:
        """Indicators restricted to accounts whose persisted verdict is ``contributeur_net``."""
        today = today or date.today()
        stmt = (
            select(
                func.count(distinct(User.id)),
                func.count(CitizenCommitment.id),
                func.sum(
                    ConsumptionCategory.reference_annual_expenditure
                    * User.household_adults
                    * cast(CitizenCommitment.reduction_percentage, Numeric(20, 8))
                    / 100
                ),
                func.avg(cast(CitizenCommitment.reduction_percentage, Numeric(20, 8))),
            )
            .select_from(CitizenCommitment)
            .join(User, User.id == CitizenCommitment.user_id)
            .join(NetBalanceVerdict, NetBalanceVerdict.user_id == User.id)
            .join(ConsumptionCategory, ConsumptionCategory.id == CitizenCommitment.category_id)
            .where(
                User.is_active.is_(True),
                User.consents[CONSENT_KEY].as_boolean().is_(True),
                NetBalanceVerdict.verdict == "contributeur_net",
                CitizenCommitment.status == "active",
                CitizenCommitment.start_date <= today,
                CitizenCommitment.end_date >= today,
                CitizenCommitment.reduction_percentage > 0,
            )
        )
        contributors, commitments, total, avg_pct = (await self._session.execute(stmt)).one()
        contributors, commitments = int(contributors or 0), int(commitments or 0)
        if contributors == 0:
            return NetContributorInputs(0, commitments, ZERO, ZERO, ZERO)
        total_d = money(Decimal(total or 0))
        return NetContributorInputs(
            contributors,
            commitments,
            total_d,
            money(total_d / contributors),
            money(Decimal(avg_pct or 0)),
        )

    async def compute(
        self, snapshot: ModelSnapshot, inputs: AggregateInputs, horizon: str = "medium"
    ) -> tuple[PersonalSavings, FiscalResult]:
        savings = aggregate_savings(snapshot, inputs.avoided_by_category)
        outcome = run_engine(snapshot, savings, horizon)
        active = {c for c, v in inputs.avoided_by_category.items() if v > 0}
        return savings, build_fiscal_result(snapshot, outcome, active, horizon)

    async def refresh(self, snapshot: ModelSnapshot, today: date | None = None) -> AggregatedImpact:
        """Compute today's national snapshot; one row per day (updated if it exists)."""
        today = today or date.today()
        inputs = await self.collect(today)
        savings, fiscal = await self.compute(snapshot, inputs)
        net = await self.collect_net_contributors(today)
        details: dict[str, Any] = {
            "fiscal": fiscal.model_dump(mode="json"),
            "dataset_versions": list(snapshot.dataset_versions),
            "net_contributors": {
                "contributors": net.contributors,
                "active_commitments": net.active_commitments,
                "total_reduction": str(net.total_reduction),
                "mean_reduction_per_contributor": str(net.mean_reduction_per_contributor),
                "mean_reduction_percentage": str(net.mean_reduction_percentage),
            },
        }
        values = {
            "participant_count": inputs.participants,
            "active_commitments": inputs.active_commitments,
            "consumption_reduction": savings.avoided_expenditure,
            "personal_savings": savings.annual_savings,
            "direct_fiscal_impact": fiscal.direct.central,
            "indirect_fiscal_impact": fiscal.indirect.central if fiscal.indirect else ZERO,
            "induced_fiscal_impact": fiscal.induced.central if fiscal.induced else ZERO,
            "net_fiscal_impact": fiscal.net_public_impact.central,
            "model_version": snapshot.model_version,
            "details": details,
            "calculated_at": datetime.now(UTC),
        }
        key = {
            "aggregation_period": "daily",
            "geographical_level": "national",
            "geographical_code": "FR",
            "snapshot_date": today,
        }
        stmt = pg_insert(AggregatedImpact).values(**key, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(key), set_={k: stmt.excluded[k] for k in values}
        )
        await self._session.execute(stmt)
        row = await self._session.scalar(
            select(AggregatedImpact)
            .where(*(getattr(AggregatedImpact, k) == v for k, v in key.items()))
            .execution_options(populate_existing=True)
        )
        assert row is not None
        return row

    # ------------------------------------------------------------------ #
    async def latest(self) -> AggregatedImpact | None:
        return await self._session.scalar(
            select(AggregatedImpact)
            .where(
                AggregatedImpact.aggregation_period == "daily",
                AggregatedImpact.geographical_level == "national",
            )
            .order_by(AggregatedImpact.calculated_at.desc(), AggregatedImpact.id.desc())
            .limit(1)
        )

    async def national(self, min_cohort: int = MIN_COHORT_SIZE) -> DashboardNational:
        """Public national dashboard; figures are withheld below ``min_cohort``."""
        row = await self.latest()
        return to_national(row, min_cohort)

    async def history(
        self, min_cohort: int = MIN_COHORT_SIZE, limit: int = 365
    ) -> DashboardHistory:
        rows = await self._session.execute(
            select(AggregatedImpact)
            .where(
                AggregatedImpact.aggregation_period == "daily",
                AggregatedImpact.geographical_level == "national",
                AggregatedImpact.participant_count >= min_cohort,
            )
            .order_by(AggregatedImpact.calculated_at.desc())
            .limit(limit)
        )
        points = [
            HistoryPoint(
                date=r.calculated_at.date(),
                participants=r.participant_count,
                net_public_impact=r.net_fiscal_impact,
                personal_savings=r.personal_savings,
            )
            for r in rows.scalars()
        ]
        return DashboardHistory(min_cohort=min_cohort, points=list(reversed(points)))

    async def sectors(self, min_cohort: int = MIN_COHORT_SIZE) -> DashboardSectors:
        row = await self.latest()
        if row is None or row.participant_count < min_cohort:
            return DashboardSectors(suppressed=True, min_cohort=min_cohort, sectors=[])
        fiscal = FiscalResult.model_validate(row.details["fiscal"])
        return DashboardSectors(
            suppressed=False, min_cohort=min_cohort, sectors=list(fiscal.top_sectors)
        )


def to_net_contributor_indicators(
    details: dict[str, Any] | None, min_cohort: int
) -> NetContributorIndicators:
    """Public net-contributor indicators; withheld when absent or below ``min_cohort``."""
    raw = (details or {}).get("net_contributors")
    contributors = raw.get("contributors") if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(contributors, int) or contributors < min_cohort:
        return NetContributorIndicators(
            suppressed=True,
            min_cohort=min_cohort,
            contributors_label=f"moins de {min_cohort}",
        )
    return NetContributorIndicators(
        suppressed=False,
        min_cohort=min_cohort,
        contributors_label=str(contributors),
        contributors=contributors,
        active_commitments=int(raw["active_commitments"]),
        total_reduction=Decimal(str(raw["total_reduction"])),
        mean_reduction_per_contributor=Decimal(str(raw["mean_reduction_per_contributor"])),
        mean_reduction_percentage=Decimal(str(raw["mean_reduction_percentage"])),
    )


def to_national(row: AggregatedImpact | None, min_cohort: int) -> DashboardNational:
    common = {
        "min_cohort": min_cohort,
        "methodology": METHODOLOGY_FISCAL,
        "net_contributors": to_net_contributor_indicators(
            row.details if row else None, min_cohort
        ),
        "disclaimers": [
            *DISCLAIMERS,
            "Les engagements agrégés sont déclaratifs et non vérifiés ; ils ne représentent pas "
            "l'ensemble de la population française.",
            "Les contributeurs nets engagés sont déclaratifs et non vérifiés : leur statut "
            "provient d'un bilan calculé sans justificatif.",
        ],
    }
    if row is None or row.participant_count < min_cohort:
        return DashboardNational(
            suppressed=True,
            participants_label=f"moins de {min_cohort}" if row else "aucun participant",
            calculated_at=row.calculated_at if row else None,
            model_version=row.model_version if row else None,
            **common,
        )
    fiscal = FiscalResult.model_validate(row.details["fiscal"])
    return DashboardNational(
        suppressed=False,
        participants_label=str(row.participant_count),
        participants=row.participant_count,
        active_commitments=row.active_commitments,
        consumption_reduction=row.consumption_reduction,
        personal_savings=row.personal_savings,
        fiscal=fiscal,
        net_public_impact_monthly=_scale(fiscal.net_public_impact, MONTHS_PER_YEAR),
        net_public_impact_daily=_scale(fiscal.net_public_impact, DAYS_PER_YEAR),
        calculated_at=row.calculated_at,
        model_version=row.model_version,
        dataset_versions=list(row.details.get("dataset_versions", [])),
        confidence=fiscal.confidence,
        **common,
    )


__all__ = [
    "MIN_COHORT_SIZE",
    "AggregateInputs",
    "AggregationService",
    "NetContributorInputs",
    "aggregate_savings",
    "to_national",
    "to_net_contributor_indicators",
]
