"""Citizen commitments and privacy-preserving aggregation on a real PostgreSQL."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.economics.defaults import bootstrap_snapshot
from app.models.user import User
from app.schemas.commitment import CommitmentCreate, CommitmentUpdate
from app.services.aggregation_service import MIN_COHORT_SIZE, AggregationService
from app.services.commitment_service import (
    CONSENT_KEY,
    CommitmentConflictError,
    CommitmentInvalidError,
    CommitmentNotFoundError,
    CommitmentService,
    add_months,
)

pytestmark = pytest.mark.integration

D = Decimal
TODAY = date(2026, 11, 7)


async def _user(
    session, n: int, *, consent: bool = True, active: bool = True, adults: int = 1
) -> User:
    user = User(
        email=f"u{n}@example.org",
        password_hash="x",
        display_name=f"U{n}",
        is_active=active,
        household_adults=adults,
        consents={CONSENT_KEY: consent},
    )
    session.add(user)
    await session.flush()
    return user


def _create(code="restaurants", pct="50", months=3, **kw) -> CommitmentCreate:
    return CommitmentCreate(
        category_code=code, reduction_percentage=D(pct), duration_months=months, **kw
    )


# ------------------------------------------------------------------ pure helpers
def test_add_months_clamps_to_month_length() -> None:
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 7), 12) == date(2027, 11, 7)
    assert add_months(date(2026, 12, 15), 3) == date(2027, 3, 15)


def test_exactly_one_duration_is_required() -> None:
    with pytest.raises(ValueError):
        CommitmentCreate(category_code="x", reduction_percentage=D("10"))
    with pytest.raises(ValueError):
        CommitmentCreate(
            category_code="x",
            reduction_percentage=D("10"),
            duration_months=3,
            end_date=date(2027, 1, 1),
        )
    with pytest.raises(ValueError):
        CommitmentCreate(category_code="x", reduction_percentage=D("101"), duration_months=3)


# ------------------------------------------------------------------ commitments
async def test_create_update_suspend_renew_delete(econ_session) -> None:
    user = await _user(econ_session, 1)
    service = CommitmentService(econ_session)

    created = await service.create(user.id, _create(months=3), TODAY)
    assert created.status == "active" and created.is_current and created.declared
    assert created.end_date == date(2027, 2, 7)

    updated = await service.update(
        user.id, created.id, CommitmentUpdate(reduction_percentage=D("75")), TODAY
    )
    assert updated.reduction_percentage == D("75")

    suspended = await service.update(
        user.id, created.id, CommitmentUpdate(status="suspended"), TODAY
    )
    assert suspended.status == "suspended" and not suspended.is_current

    later = TODAY + timedelta(days=200)  # the commitment has expired by then
    renewed = await service.update(user.id, created.id, CommitmentUpdate(renew=True), later)
    assert renewed.status == "active" and renewed.start_date == later
    assert renewed.end_date == add_months(later, 3)  # same length as before

    await service.delete(user.id, created.id)
    assert await service.list_for(user.id, TODAY) == []


async def test_one_commitment_per_category(econ_session) -> None:
    user = await _user(econ_session, 1)
    service = CommitmentService(econ_session)
    await service.create(user.id, _create(), TODAY)
    with pytest.raises(CommitmentConflictError):
        await service.create(user.id, _create(pct="20"), TODAY)


async def test_invalid_category_and_durations(econ_session) -> None:
    user = await _user(econ_session, 1)
    service = CommitmentService(econ_session)
    with pytest.raises(CommitmentInvalidError):
        await service.create(user.id, _create(code="yachts"), TODAY)
    with pytest.raises(CommitmentInvalidError):
        await service.create(
            user.id,
            CommitmentCreate(
                category_code="clothing", reduction_percentage=D("10"), end_date=TODAY
            ),
            TODAY,
        )
    with pytest.raises(CommitmentInvalidError):
        await service.create(
            user.id,
            CommitmentCreate(
                category_code="clothing",
                reduction_percentage=D("10"),
                end_date=add_months(TODAY, 37),
            ),
            TODAY,
        )
    custom = await service.create(
        user.id,
        CommitmentCreate(
            category_code="clothing",
            reduction_percentage=D("10"),
            end_date=TODAY + timedelta(days=45),
        ),
        TODAY,
    )
    assert custom.end_date == TODAY + timedelta(days=45)


async def test_users_cannot_touch_each_others_commitments(econ_session) -> None:
    owner, intruder = await _user(econ_session, 1), await _user(econ_session, 2)
    service = CommitmentService(econ_session)
    created = await service.create(owner.id, _create(), TODAY)
    with pytest.raises(CommitmentNotFoundError):
        await service.update(intruder.id, created.id, CommitmentUpdate(status="suspended"), TODAY)
    with pytest.raises(CommitmentNotFoundError):
        await service.delete(intruder.id, created.id)
    assert await service.list_for(intruder.id, TODAY) == []


# ------------------------------------------------------------------ aggregation
async def _populate(session, consenting: int, *, extra_noise: bool = True) -> None:
    service = CommitmentService(session)
    for n in range(consenting):
        user = await _user(session, n)
        await service.create(user.id, _create(pct="50"), date.today())
    if extra_noise:
        no_consent = await _user(session, 900, consent=False)
        inactive = await _user(session, 901, active=False)
        suspended = await _user(session, 902)
        expired = await _user(session, 903)
        for other in (no_consent, inactive):
            await service.create(other.id, _create(pct="100"), date.today())
        created = await service.create(suspended.id, _create(pct="100"), date.today())
        await service.update(suspended.id, created.id, CommitmentUpdate(status="suspended"))
        await service.create(
            expired.id,
            CommitmentCreate(
                category_code="restaurants",
                reduction_percentage=D("100"),
                start_date=date.today() - timedelta(days=90),
                end_date=date.today() - timedelta(days=1),
            ),
            date.today(),
        )


async def test_only_consenting_active_current_commitments_are_counted(econ_session) -> None:
    await _populate(econ_session, 25)
    inputs = await AggregationService(econ_session).collect()
    assert inputs.participants == 25
    assert inputs.active_commitments == 25
    assert inputs.avoided_by_category == {"restaurants": D("18750")}  # 25 x 50 % x 1500


async def test_household_size_weights_the_aggregate(econ_session) -> None:
    service = CommitmentService(econ_session)
    for n, adults in enumerate((1, 2, 3)):
        user = await _user(econ_session, n, adults=adults)
        await service.create(user.id, _create(code="clothing", pct="100"), date.today())
    inputs = await AggregationService(econ_session).collect()
    assert inputs.avoided_by_category["clothing"] == D("2820")  # 470 x (1 + 2 + 3)


async def test_public_dashboard_withholds_figures_below_the_cohort_threshold(econ_session) -> None:
    await _populate(econ_session, MIN_COHORT_SIZE - 1, extra_noise=False)
    service = AggregationService(econ_session)
    await service.refresh(bootstrap_snapshot())
    below = await service.national()
    assert below.suppressed and below.participants is None and below.fiscal is None
    assert below.participants_label == f"moins de {MIN_COHORT_SIZE}"
    assert below.personal_savings is None and below.consumption_reduction is None
    assert (await service.history()).points == []
    assert (await service.sectors()).suppressed

    extra = await _user(econ_session, 5000)
    await CommitmentService(econ_session).create(extra.id, _create(pct="50"), date.today())
    await service.refresh(bootstrap_snapshot())
    above = await service.national()
    assert not above.suppressed and above.participants == MIN_COHORT_SIZE
    assert above.fiscal is not None and above.fiscal.net_public_impact.central > 0
    assert above.confidence == "provisional" and above.dataset_versions
    assert above.net_public_impact_monthly is not None
    assert above.net_public_impact_monthly.central == (
        above.fiscal.net_public_impact.central / 12
    ).quantize(D("0.01"))


async def test_refresh_keeps_one_row_per_day(econ_session) -> None:
    await _populate(econ_session, 3, extra_noise=False)
    service = AggregationService(econ_session)
    first = await service.refresh(bootstrap_snapshot())
    second = await service.refresh(bootstrap_snapshot())
    assert first.id == second.id


async def test_aggregate_equals_sum_of_individual_runs(econ_session) -> None:
    from app.economics.engine import run_engine
    from app.economics.personal import compute_personal_savings

    await _populate(econ_session, 25, extra_noise=False)
    snapshot = bootstrap_snapshot()
    service = AggregationService(econ_session)
    inputs = await service.collect()
    _, fiscal = await service.compute(snapshot, inputs)
    single = run_engine(snapshot, compute_personal_savings(snapshot, {"restaurants": D("50")}))
    assert fiscal.net_public_impact.central == pytest.approx(
        25 * single.central.net_public_impact, abs=D("1.00")
    )


async def test_dashboard_exposes_no_individual_data(econ_session) -> None:
    await _populate(econ_session, MIN_COHORT_SIZE, extra_noise=False)
    service = AggregationService(econ_session)
    await service.refresh(bootstrap_snapshot())
    payload = (await service.national()).model_dump_json()
    for forbidden in ("email", "u0@example.org", "user_id", "display_name", "region"):
        assert forbidden not in payload
