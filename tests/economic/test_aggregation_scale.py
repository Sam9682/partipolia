"""Aggregation at scale: 10 000 to 1 million participants (pure engine, no database)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.defaults import bootstrap_snapshot
from app.economics.engine import run_engine
from app.economics.personal import compute_personal_savings
from app.services.aggregation_service import aggregate_savings

D = Decimal
PLAN = {"restaurants": D("50"), "clothing": D("30"), "leisure": D("20")}


def _per_participant():
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, PLAN)
    return snapshot, savings, {c.code: c.avoided_expenditure for c in savings.categories}


@pytest.mark.parametrize("participants", [10_000, 100_000, 500_000, 1_000_000])
def test_scaling_is_linear_and_exact_enough(participants: int) -> None:
    snapshot, one, per_user = _per_participant()
    total = aggregate_savings(snapshot, {k: v * participants for k, v in per_user.items()})
    assert total.annual_savings == one.annual_savings * participants

    aggregate = run_engine(snapshot, total).central
    single = run_engine(snapshot, one).central
    # The model is linear: aggregate = N x individual. The single run is rounded to the
    # cent, so N x single may differ by at most half a cent per participant; the
    # aggregate, computed once at full precision, is the accurate figure.
    tolerance = Decimal("0.005") * participants + Decimal("1")
    assert abs(aggregate.net_public_impact - participants * single.net_public_impact) <= tolerance
    assert aggregate.net_public_impact > 0
    assert aggregate.gross_revenue_loss < total.annual_savings  # revenue is not savings


def test_scenario_ordering_holds_at_one_million() -> None:
    snapshot, _, per_user = _per_participant()
    total = aggregate_savings(snapshot, {k: v * 1_000_000 for k, v in per_user.items()})
    outcome = run_engine(snapshot, total)
    assert outcome.low.net_public_impact <= outcome.central.net_public_impact
    assert outcome.central.net_public_impact <= outcome.high.net_public_impact


def test_aggregation_is_deterministic() -> None:
    snapshot, _, per_user = _per_participant()
    total = aggregate_savings(snapshot, {k: v * 123_457 for k, v in per_user.items()})
    assert run_engine(snapshot, total) == run_engine(snapshot, total)


def test_zero_participants_yield_zero_impact() -> None:
    snapshot = bootstrap_snapshot()
    outcome = run_engine(snapshot, aggregate_savings(snapshot, {}))
    assert outcome.central.net_public_impact == D("0.00")
