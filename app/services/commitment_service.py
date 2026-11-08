"""Citizen commitments: create, modify, suspend, renew, delete (declared, unverified)."""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.economics.decimal_utils import ZERO
from app.models.citizen_commitment import CitizenCommitment
from app.models.consumption_category import ConsumptionCategory
from app.models.user import User
from app.reference.regions import is_valid_region
from app.schemas.commitment import (
    CommitmentCreate,
    CommitmentOut,
    CommitmentPlan,
    CommitmentPlanResult,
    CommitmentUpdate,
    plan_outcome,
)

CONSENT_KEY = "aggregate_commitments"
MAX_CUSTOM_MONTHS = 36


class CommitmentError(Exception):
    """Base class of expected, user-facing commitment errors."""


class CommitmentNotFoundError(CommitmentError):
    pass


class CommitmentConflictError(CommitmentError):
    pass


class CommitmentInvalidError(CommitmentError):
    pass


def add_months(start: date, months: int) -> date:
    """``start`` plus ``months`` calendar months (day clamped to the month length)."""
    index = start.month - 1 + months
    year, month = start.year + index // 12, index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def _validate_end(start: date, end: date) -> None:
    if end <= start:
        raise CommitmentInvalidError("end date must be after the start date")
    if end > add_months(start, MAX_CUSTOM_MONTHS):
        raise CommitmentInvalidError(f"a commitment cannot exceed {MAX_CUSTOM_MONTHS} months")


def to_out(
    commitment: CitizenCommitment, category: ConsumptionCategory, today: date
) -> CommitmentOut:
    return CommitmentOut(
        id=commitment.id,
        category_code=category.code,
        category_name=category.name,
        reduction_percentage=commitment.reduction_percentage,
        start_date=commitment.start_date,
        end_date=commitment.end_date,
        status=commitment.status,
        is_current=(
            commitment.status == "active" and commitment.start_date <= today <= commitment.end_date
        ),
    )


class CommitmentService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_for(self, user_id: int, today: date | None = None) -> list[CommitmentOut]:
        today = today or date.today()
        rows = await self._session.execute(
            select(CitizenCommitment, ConsumptionCategory)
            .join(ConsumptionCategory, CitizenCommitment.category_id == ConsumptionCategory.id)
            .where(CitizenCommitment.user_id == user_id)
            .order_by(ConsumptionCategory.id)
        )
        return [to_out(c, cat, today) for c, cat in rows.all()]

    async def create(
        self, user_id: int, payload: CommitmentCreate, today: date | None = None
    ) -> CommitmentOut:
        today = today or date.today()
        category = await self._category(payload.category_code)
        start = payload.start_date or today
        end = (
            add_months(start, payload.duration_months)
            if payload.duration_months
            else payload.end_date
        )
        assert end is not None
        _validate_end(start, end)
        commitment = CitizenCommitment(
            user_id=user_id,
            category_id=category.id,
            reduction_percentage=payload.reduction_percentage,
            start_date=start,
            end_date=end,
            status="active",
        )
        self._session.add(commitment)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise CommitmentConflictError(
                "a commitment already exists for this category; modify it instead"
            ) from exc
        return to_out(commitment, category, today)

    async def update(
        self,
        user_id: int,
        commitment_id: int,
        payload: CommitmentUpdate,
        today: date | None = None,
    ) -> CommitmentOut:
        today = today or date.today()
        commitment, category = await self._owned(user_id, commitment_id)
        if payload.reduction_percentage is not None:
            commitment.reduction_percentage = payload.reduction_percentage
        if payload.status is not None:
            commitment.status = payload.status
        if payload.renew or payload.duration_months or payload.end_date:
            start = commitment.start_date
            if payload.renew:
                # Renewing restarts the commitment today and makes it active again.
                start = today
                commitment.status = "active"
            if payload.end_date is not None:
                end = payload.end_date
            else:
                end = add_months(start, payload.duration_months or _span_months(commitment))
            _validate_end(start, end)
            commitment.start_date, commitment.end_date = start, end
        await self._session.flush()
        return to_out(commitment, category, today)

    async def delete(self, user_id: int, commitment_id: int) -> None:
        commitment, _ = await self._owned(user_id, commitment_id)
        await self._session.delete(commitment)
        await self._session.flush()

    async def save_plan(
        self, user_id: int, plan: CommitmentPlan, today: date | None = None
    ) -> CommitmentPlanResult:
        """Create, update or delete one commitment per plan item (0 % removes it)."""
        today = today or date.today()
        codes = [item.category_code for item in plan.items]
        categories = {
            cat.code: cat
            for cat in (
                await self._session.scalars(
                    select(ConsumptionCategory).where(
                        ConsumptionCategory.code.in_(codes), ConsumptionCategory.active.is_(True)
                    )
                )
            ).all()
        }
        # Validate everything before writing anything.
        if any(code not in categories for code in codes):
            raise CommitmentInvalidError("unknown category")

        existing_rows = await self._session.scalars(
            select(CitizenCommitment).where(CitizenCommitment.user_id == user_id)
        )
        by_category_id = {c.category_id: c for c in existing_rows.all()}

        created = updated = deleted = 0
        end = add_months(today, plan.duration_months)
        for item in plan.items:
            category = categories[item.category_code]
            current = by_category_id.get(category.id)
            if item.reduction_percentage > ZERO:
                if current is not None:
                    current.reduction_percentage = item.reduction_percentage
                    updated += 1
                else:
                    self._session.add(
                        CitizenCommitment(
                            user_id=user_id,
                            category_id=category.id,
                            reduction_percentage=item.reduction_percentage,
                            start_date=today,
                            end_date=end,
                            status="active",
                        )
                    )
                    created += 1
            elif current is not None:
                await self._session.delete(current)
                deleted += 1
        try:
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise CommitmentConflictError(
                "a commitment already exists for this category; modify it instead"
            ) from exc
        return CommitmentPlanResult(
            outcome=plan_outcome(created, updated, deleted),
            created=created,
            updated=updated,
            deleted=deleted,
            commitments=await self.list_for(user_id, today),
        )

    # ------------------------------------------------------------------ #
    async def _category(self, code: str) -> ConsumptionCategory:
        category = await self._session.scalar(
            select(ConsumptionCategory).where(
                ConsumptionCategory.code == code, ConsumptionCategory.active.is_(True)
            )
        )
        if category is None:
            raise CommitmentInvalidError("unknown category")
        return category

    async def _owned(
        self, user_id: int, commitment_id: int
    ) -> tuple[CitizenCommitment, ConsumptionCategory]:
        row = (
            await self._session.execute(
                select(CitizenCommitment, ConsumptionCategory)
                .join(ConsumptionCategory, CitizenCommitment.category_id == ConsumptionCategory.id)
                .where(CitizenCommitment.id == commitment_id, CitizenCommitment.user_id == user_id)
            )
        ).first()
        if row is None:  # same answer whether it does not exist or belongs to someone else
            raise CommitmentNotFoundError("commitment not found")
        return row[0], row[1]


def _span_months(commitment: CitizenCommitment) -> int:
    """Whole months between start and end (at least 1): used to renew with the same length."""
    months = (commitment.end_date.year - commitment.start_date.year) * 12 + (
        commitment.end_date.month - commitment.start_date.month
    )
    return max(1, months)


def validate_profile_region(region: str | None) -> str | None:
    """Return `region` if null or in the versioned Region list, else raise (field `region`)."""
    if region is not None and not is_valid_region(region):
        raise CommitmentInvalidError("region : code de Région inconnu.")
    return region


async def get_profile(session: AsyncSession, user_id: int) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise CommitmentNotFoundError("user not found")
    return user


def consent_of(user: User) -> bool:
    return bool((user.consents or {}).get(CONSENT_KEY, False))


def reductions_of(commitments: list[CommitmentOut]) -> dict[str, Decimal]:
    """Reduction percentages of the commitments that are currently in force."""
    return {c.category_code: c.reduction_percentage for c in commitments if c.is_current}
