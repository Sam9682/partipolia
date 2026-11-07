"""Regional aggregation of declared commitments (computation side).

Regional figures are stored *complete*; the publication rules (primary and
complementary suppression) are applied at read time by ``app.economics.disclosure``.

Exactness: the national avoided spending per category is the exact sum of the
grouped amounts (regions + the "no valid region" group). It is split back by
largest remainder (:func:`apportion`) so that Σ regions + unassigned equals the
national figure to the cent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Numeric, cast, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.economics import disclosure
from app.economics.aggregate import aggregate_savings, apportion
from app.economics.decimal_utils import ZERO, money
from app.economics.engine import run_engine
from app.economics.types import ModelSnapshot
from app.models.aggregation import AggregatedImpact
from app.models.citizen_commitment import CitizenCommitment
from app.models.consumption_category import ConsumptionCategory
from app.models.user import User
from app.reference.regions import load_regions
from app.schemas.dashboard import (
    RegionalHistory,
    RegionalHistoryPoint,
    RegionalList,
    RegionCategory,
)
from app.schemas.dashboard import RegionalView as RegionalViewOut
from app.schemas.simulation import FiscalResult
from app.services.aggregation_service import MIN_COHORT_SIZE, AggregationService
from app.services.audit_service import AuditService
from app.services.commitment_service import CONSENT_KEY
from app.services.simulation_presenter import DISCLAIMERS, METHODOLOGY_FISCAL, build_fiscal_result

logger = get_logger(__name__)

#: Key of the "no valid region" group.
UNASSIGNED: None = None

REGION_DETAIL_WITHHELD_ACTION = "dashboard.region_detail_denied"


class RegionalRefreshError(RuntimeError):
    """Every region failed to refresh."""


class RegionNotFoundError(LookupError):
    """Unknown Region code (=> 404, Requirement 5.7)."""


class RegionWithheldError(PermissionError):
    """Withheld Region requested through the admin endpoint (=> 403, no figure)."""

    def __init__(self, code: str) -> None:
        super().__init__("region withheld")
        self.code = code


@dataclass(slots=True)
class GroupInputs:
    participants: int = 0
    active_commitments: int = 0
    avoided_by_category: dict[str, Decimal] = field(default_factory=dict)  # EUR / year, TTC
    participants_by_category: dict[str, int] = field(default_factory=dict)


@dataclass(slots=True)
class GroupedInputs:
    #: Valid region code -> inputs; ``None`` -> citizens without a valid region.
    groups: dict[str | None, GroupInputs] = field(default_factory=dict)

    def national_avoided(self) -> dict[str, Decimal]:
        total: dict[str, Decimal] = {}
        for group in self.groups.values():
            for code, value in group.avoided_by_category.items():
                total[code] = total.get(code, ZERO) + value
        return total


@dataclass(slots=True)
class RegionalRefreshReport:
    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


def _jsonable(values: dict[str, Decimal]) -> dict[str, str]:
    return {k: str(v) for k, v in values.items()}


class RegionalAggregationService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    async def collect_grouped(self, today: date | None = None) -> GroupedInputs:
        """One ``GROUP BY`` query on region × category (same filters as the national one)."""
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
        code_col = ConsumptionCategory.code
        # GROUPING SETS gives, in a single query, the per (region, category) rows
        # and the per-region rows (code is NULL there) with distinct participants.
        stmt = (
            select(
                User.region,
                code_col,
                func.grouping(code_col),
                func.sum(
                    ConsumptionCategory.reference_annual_expenditure
                    * User.household_adults
                    * cast(CitizenCommitment.reduction_percentage, Numeric(20, 8))
                    / 100
                ),
                func.count(CitizenCommitment.id),
                func.count(func.distinct(User.id)),
            )
            .select_from(ConsumptionCategory)
            .join(CitizenCommitment, CitizenCommitment.category_id == ConsumptionCategory.id)
            .join(User, User.id == CitizenCommitment.user_id)
            .where(*consented, *current)
            .group_by(
                func.grouping_sets(
                    tuple_(User.region, code_col),
                    tuple_(User.region),
                )
            )
        )
        valid = {r.code for r in load_regions().regions}
        grouped = GroupedInputs()
        for region, code, is_total, total, commitments, participants in (
            await self._session.execute(stmt)
        ).all():
            key = region if region in valid else UNASSIGNED
            group = grouped.groups.setdefault(key, GroupInputs())
            if is_total:
                # Each user has exactly one region: per-region distinct counts add up.
                group.participants += int(participants)
                group.active_commitments += int(commitments)
            else:
                group.avoided_by_category[code] = group.avoided_by_category.get(
                    code, ZERO
                ) + Decimal(total or 0)
                group.participants_by_category[code] = group.participants_by_category.get(
                    code, 0
                ) + int(participants)
        return grouped

    # ------------------------------------------------------------------ #
    async def refresh(
        self, snapshot: ModelSnapshot, today: date | None = None
    ) -> RegionalRefreshReport:
        """Compute and upsert one snapshot per Region (SAVEPOINT each).

        A failing Region is rolled back, logged (``regional_refresh_failed``) and its
        previous snapshot is kept. Raises :class:`RegionalRefreshError` if all fail.
        """
        today = today or date.today()
        grouped = await self.collect_grouped(today)
        regions = load_regions().regions
        for region in regions:
            grouped.groups.setdefault(region.code, GroupInputs())
        grouped.groups.setdefault(UNASSIGNED, GroupInputs())

        national = grouped.national_avoided()
        shares: dict[str | None, dict[str, Decimal]] = {k: {} for k in grouped.groups}
        for code, total in national.items():
            parts = {k: g.avoided_by_category.get(code, ZERO) for k, g in grouped.groups.items()}
            for key, value in apportion(total, parts).items():
                shares[key][code] = value

        report = RegionalRefreshReport()
        for region in regions:
            try:
                async with self._session.begin_nested():
                    await self._write_region(
                        snapshot,
                        today,
                        region.code,
                        region.label,
                        grouped.groups[region.code],
                        shares[region.code],
                    )
                report.succeeded.append(region.code)
            except Exception:
                logger.exception("regional_refresh_failed", region=region.code)
                report.failed.append(region.code)
        if not report.succeeded:
            raise RegionalRefreshError("all regional refreshes failed")

        await self._update_national(snapshot, today, grouped, national, shares[UNASSIGNED])
        return report

    # ------------------------------------------------------------------ #
    async def _write_region(
        self,
        snapshot: ModelSnapshot,
        today: date,
        code: str,
        label: str,
        group: GroupInputs,
        avoided: dict[str, Decimal],
    ) -> None:
        savings = aggregate_savings(snapshot, avoided)
        outcome = run_engine(snapshot, savings, "medium")
        active = {c for c, v in avoided.items() if v > 0}
        fiscal = build_fiscal_result(snapshot, outcome, active, "medium")
        details: dict[str, Any] = {
            "fiscal": fiscal.model_dump(mode="json"),
            "dataset_versions": list(snapshot.dataset_versions),
            "avoided_by_category": _jsonable(avoided),
            "participants_by_category": dict(group.participants_by_category),
            "region_label": label,
        }
        values = {
            "participant_count": group.participants,
            "active_commitments": group.active_commitments,
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
            "geographical_level": "region",
            "geographical_code": code,
            "snapshot_date": today,
        }
        stmt = pg_insert(AggregatedImpact).values(**key, **values)
        stmt = stmt.on_conflict_do_update(
            index_elements=list(key), set_={k: stmt.excluded[k] for k in values}
        )
        await self._session.execute(stmt)

    async def _update_national(
        self,
        snapshot: ModelSnapshot,
        today: date,
        grouped: GroupedInputs,
        national: dict[str, Decimal],
        unassigned_avoided: dict[str, Decimal],
    ) -> None:
        """Add ``by_category`` and ``unassigned`` to today's national row."""
        where = (
            AggregatedImpact.aggregation_period == "daily",
            AggregatedImpact.geographical_level == "national",
            AggregatedImpact.geographical_code == "FR",
            AggregatedImpact.snapshot_date == today,
        )
        row = await self._session.scalar(
            select(AggregatedImpact).where(*where).execution_options(populate_existing=True)
        )
        if row is None:
            row = await AggregationService(self._session).refresh(snapshot, today)

        # The model is linear: each category's contribution is the engine run on it alone.
        by_category: dict[str, Any] = {}
        participants: dict[str, int] = {}
        for group in grouped.groups.values():
            for code, count in group.participants_by_category.items():
                participants[code] = participants.get(code, 0) + count
        for code, value in national.items():
            if value <= 0:
                continue
            exact = money(value)
            outcome = run_engine(snapshot, aggregate_savings(snapshot, {code: exact}), "medium")
            by_category[code] = {
                "participants": participants.get(code, 0),
                "avoided_expenditure": str(exact),
                "net_public_impact": str(money(outcome.central.net_public_impact)),
            }
        unassigned = grouped.groups[UNASSIGNED]
        row.details = {
            **row.details,
            "by_category": by_category,
            "unassigned": {
                "participants": unassigned.participants,
                "active_commitments": unassigned.active_commitments,
                "avoided_by_category": _jsonable(unassigned_avoided),
                "participants_by_category": dict(unassigned.participants_by_category),
            },
        }
        await self._session.flush()

    # ------------------------------------------------------------------ #
    # Read side: stored aggregates only (no recomputation), published     #
    # exclusively through ``app.economics.disclosure``.                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _label(code: str) -> str:
        for region in load_regions().regions:
            if region.code == code:
                return region.label
        raise RegionNotFoundError(code)

    @staticmethod
    def _to_region_row(code: str, label: str, row: AggregatedImpact | None) -> disclosure.RegionRow:
        if row is None:
            return disclosure.RegionRow(code, label, 0, ZERO)
        details = row.details or {}
        participants_by = details.get("participants_by_category", {})
        avoided_by = details.get("avoided_by_category", {})
        cells = tuple(
            disclosure.Cohort(cat, int(participants_by.get(cat, 0)), Decimal(str(amount)))
            for cat, amount in sorted(avoided_by.items())
        )
        return disclosure.RegionRow(
            code, label, row.participant_count, row.consumption_reduction, cells
        )

    @staticmethod
    def _to_national_row(row: AggregatedImpact | None) -> disclosure.NationalRow | None:
        if row is None:
            return None
        unassigned = (row.details or {}).get("unassigned", {})
        return disclosure.NationalRow(row.participant_count, int(unassigned.get("participants", 0)))

    async def _latest_regional_rows(self) -> dict[str, AggregatedImpact]:
        stmt = (
            select(AggregatedImpact)
            .where(
                AggregatedImpact.aggregation_period == "daily",
                AggregatedImpact.geographical_level == "region",
            )
            .distinct(AggregatedImpact.geographical_code)
            .order_by(AggregatedImpact.geographical_code, AggregatedImpact.snapshot_date.desc())
        )
        return {r.geographical_code: r for r in (await self._session.scalars(stmt)).all()}

    async def _latest_national_row(self) -> AggregatedImpact | None:
        return await AggregationService(self._session).latest()

    def _render(
        self,
        view: disclosure.RegionalView,
        row: AggregatedImpact | None,
        min_cohort: int,
    ) -> RegionalViewOut:
        common = {
            "code": view.code,
            "label": view.label,
            "min_cohort": min_cohort,
            "methodology": METHODOLOGY_FISCAL,
            "disclaimers": [
                *DISCLAIMERS,
                "Les engagements agrégés sont déclaratifs et non vérifiés ; ils ne représentent "
                "pas l'ensemble de la population de la Région.",
            ],
        }
        if view.status == "suppressed" or row is None:
            return RegionalViewOut(status="suppressed", **common)
        return RegionalViewOut(
            status="published",
            participants=view.participants,
            active_commitments=row.active_commitments,
            consumption_reduction=view.amount,
            personal_savings=row.personal_savings,
            fiscal=FiscalResult.model_validate(row.details["fiscal"]),
            categories=[
                RegionCategory(
                    code=c.key, participants=c.participants, avoided_expenditure=c.amount
                )
                for c in view.categories
            ],
            model_version=row.model_version,
            calculated_at=row.calculated_at,
            **common,
        )

    async def _published_views(
        self, min_cohort: int
    ) -> tuple[list[RegionalViewOut], dict[str, AggregatedImpact]]:
        rows = await self._latest_regional_rows()
        national = self._to_national_row(await self._latest_national_row())
        region_rows = [
            self._to_region_row(r.code, r.label, rows.get(r.code)) for r in load_regions().regions
        ]
        views = disclosure.publish_regions(region_rows, national, min_cohort)
        return [self._render(v, rows.get(v.code), min_cohort) for v in views], rows

    async def list_regions(self, min_cohort: int = MIN_COHORT_SIZE) -> RegionalList:
        views, _ = await self._published_views(min_cohort)
        return RegionalList(min_cohort=min_cohort, regions=views)

    async def detail(self, code: str, min_cohort: int = MIN_COHORT_SIZE) -> RegionalViewOut:
        self._label(code)  # 404 for an unknown code
        views, _ = await self._published_views(min_cohort)
        return next(v for v in views if v.code == code)

    async def history(
        self, code: str, min_cohort: int = MIN_COHORT_SIZE, limit: int = 365
    ) -> RegionalHistory:
        """Chronological points; snapshots below ``min_cohort`` (or withheld) are omitted."""
        label = self._label(code)
        stmt = (
            select(AggregatedImpact)
            .where(
                AggregatedImpact.aggregation_period == "daily",
                AggregatedImpact.geographical_level == "region",
                AggregatedImpact.geographical_code == code,
                AggregatedImpact.participant_count >= min_cohort,
            )
            .order_by(AggregatedImpact.snapshot_date.desc())
            .limit(limit)
        )
        own = list((await self._session.scalars(stmt)).all())
        if not own:
            return RegionalHistory(code=code, label=label, min_cohort=min_cohort, points=[])

        dates = [r.snapshot_date for r in own]
        siblings = (
            await self._session.scalars(
                select(AggregatedImpact).where(
                    AggregatedImpact.aggregation_period == "daily",
                    AggregatedImpact.geographical_level.in_(("region", "national")),
                    AggregatedImpact.snapshot_date.in_(dates),
                )
            )
        ).all()
        by_date: dict[date, dict[str, AggregatedImpact]] = {}
        national_by_date: dict[date, AggregatedImpact] = {}
        for r in siblings:
            if r.geographical_level == "national":
                national_by_date[r.snapshot_date] = r
            else:
                by_date.setdefault(r.snapshot_date, {})[r.geographical_code] = r

        points: list[RegionalHistoryPoint] = []
        for snap in own:
            day_rows = by_date.get(snap.snapshot_date, {})
            region_rows = [
                self._to_region_row(r.code, r.label, day_rows.get(r.code))
                for r in load_regions().regions
            ]
            views = disclosure.publish_regions(
                region_rows,
                self._to_national_row(national_by_date.get(snap.snapshot_date)),
                min_cohort,
            )
            if any(v.code == code and v.status == "published" for v in views):
                points.append(
                    RegionalHistoryPoint(
                        date=snap.snapshot_date,
                        participants=snap.participant_count,
                        net_public_impact=snap.net_fiscal_impact,
                        personal_savings=snap.personal_savings,
                    )
                )
        points.reverse()
        return RegionalHistory(code=code, label=label, min_cohort=min_cohort, points=points)

    async def admin_detail(
        self,
        code: str,
        actor: User,
        ip_hash: str | None,
        min_cohort: int = MIN_COHORT_SIZE,
    ) -> RegionalViewOut:
        """Super-admin detail: same as public, but a withheld Region gives 403 + audit."""
        view = await self.detail(code, min_cohort)
        if view.status == "suppressed":
            await AuditService(self._session).record_isolated(
                action=REGION_DETAIL_WITHHELD_ACTION,
                entity_type="region",
                new_data={"region_code": code},
                ip_hash=ip_hash,
                actor_id=actor.id,
            )
            raise RegionWithheldError(code)
        return view


__all__ = [
    "REGION_DETAIL_WITHHELD_ACTION",
    "GroupInputs",
    "GroupedInputs",
    "RegionNotFoundError",
    "RegionWithheldError",
    "RegionalAggregationService",
    "RegionalRefreshError",
    "RegionalRefreshReport",
]
