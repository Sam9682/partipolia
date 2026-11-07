"""Combined engine on the shipped INSEE-based dataset: invariants and regressions."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.categories import CATEGORY_CODES
from app.economics.defaults import bootstrap_snapshot
from app.economics.engine import run_engine
from app.economics.parameters import available_modules
from app.economics.personal import compute_personal_savings

D = Decimal
SPEC_EXAMPLE = {
    "restaurants": D("100"),
    "clothing": D("50"),
    "leisure": D("75"),
    "hotels_holidays": D("50"),
    "electronics": D("30"),
    "digital_subscriptions": D("100"),
}


def _run(reductions, adults=1, horizon="medium"):
    snapshot = bootstrap_snapshot()
    return run_engine(snapshot, compute_personal_savings(snapshot, reductions, adults), horizon)


def test_all_modules_are_available_with_shipped_data() -> None:
    assert all(available_modules(bootstrap_snapshot()).values())


def test_scenarios_are_ordered_low_central_high() -> None:
    outcome = _run(SPEC_EXAMPLE)
    for attribute in (
        "direct_total",
        "indirect_total",
        "induced_total",
        "gross_revenue_loss",
        "additional_public_expenditure",
        "net_public_impact",
    ):
        low, central, high = (
            getattr(outcome.scenario(s), attribute) for s in ("low", "central", "high")
        )
        assert low <= central <= high, attribute


def test_net_formula_and_separate_indicators() -> None:
    central = _run(SPEC_EXAMPLE).central
    assert central.gross_revenue_loss == pytest.approx(
        central.direct_total + central.indirect_total + central.induced_total, abs=D("0.02")
    )
    assert central.net_public_impact == pytest.approx(
        central.gross_revenue_loss + central.additional_public_expenditure - central.rebound_offset,
        abs=D("0.02"),
    )
    savings = compute_personal_savings(bootstrap_snapshot(), SPEC_EXAMPLE).annual_savings
    assert savings == D("3015.00")
    assert central.gross_revenue_loss < savings  # tax revenue is not personal savings
    assert central.indirect is not None
    assert (
        central.indirect.turnover_change != central.indirect.value_added_total
    )  # distinct indicators


def test_zero_reduction_has_no_impact() -> None:
    outcome = _run({})
    for name in ("low", "central", "high"):
        s = outcome.scenario(name)
        assert s.net_public_impact == D("0.00") and s.gross_revenue_loss == D("0.00")


def test_full_reduction_is_finite_and_positive() -> None:
    outcome = _run({c: D("100") for c in CATEGORY_CODES})
    assert outcome.central.gross_revenue_loss > 0
    assert outcome.high.net_public_impact > outcome.low.net_public_impact


def test_impact_is_linear_in_households() -> None:
    one = _run(SPEC_EXAMPLE, adults=1).central
    four = _run(SPEC_EXAMPLE, adults=4).central
    assert four.gross_revenue_loss == pytest.approx(4 * one.gross_revenue_loss, abs=D("0.10"))


def test_deterministic() -> None:
    assert _run(SPEC_EXAMPLE) == _run(SPEC_EXAMPLE)


def test_clothing_leaks_abroad_more_than_restaurants() -> None:
    restaurants = _run({"restaurants": D("100")}).central
    clothing = _run({"clothing": D("100")}).central
    assert restaurants.indirect is not None and clothing.indirect is not None
    r_ratio = restaurants.indirect.import_leakage / restaurants.direct.turnover_ht
    c_ratio = clothing.indirect.import_leakage / clothing.direct.turnover_ht
    assert c_ratio > D("0.25") > r_ratio  # INSEE: ~68 % of clothing resources are imported


def test_employment_horizons_increase_induced_effects() -> None:
    short, medium, long = (
        _run(SPEC_EXAMPLE, horizon=h).central.induced_total for h in ("short", "medium", "long")
    )
    assert short < medium < long


def test_components_are_split_by_public_sector() -> None:
    sectors = _run(SPEC_EXAMPLE).central.by_public_sector()
    assert set(sectors) == {"central", "local", "social_security"}
    assert sectors["central"] > 0 and sectors["social_security"] > 0
    total = sum(sectors.values())
    central = _run(SPEC_EXAMPLE).central
    assert total == pytest.approx(
        central.gross_revenue_loss + central.additional_public_expenditure, abs=D("0.10")
    )


def test_fuel_is_not_double_counted_with_vat_of_goods() -> None:
    central = _run(SPEC_EXAMPLE).central
    assert central.fuel is not None
    assert central.direct_total == pytest.approx(
        central.direct.vat + central.fuel.tax_revenue_loss, abs=D("0.02")
    )


def test_regression_reference_values() -> None:
    """Documented reference run (spec example, 1 adult, medium horizon, central).

    Values depend on the shipped dataset; update them deliberately, together with
    the model version, when the datasets change.
    """
    central = _run(SPEC_EXAMPLE).central
    assert central.direct.avoided_ttc == D("3015.00")
    # Hand check: 153.41 (restaurants) + 39.17 (clothing) + 46.73 (leisure)
    # + 39.77 (hotels) + 30.00 (electronics) + 50.00 (digital) = 359.08
    assert central.direct.vat == D("359.08")
    # Printed by `PYTHONPATH=. python tools/print_reference_run.py` (central scenario).
    assert central.direct_total == D("419.61")  # VAT 359.08 + fuel taxes 60.53
    assert central.indirect_total == D("129.22")  # production tax 78.63 + corporate tax 50.59
    assert central.induced_total == D("239.74")  # contributions and income tax
    assert central.additional_public_expenditure == D("135.57")  # benefits + partial activity
    assert central.rebound_offset == D("138.74")
    assert central.net_public_impact == D("785.40")
    assert central.indirect is not None
    assert central.indirect.turnover_change == D("4512.25")
    assert central.indirect.value_added_total == D("1965.74")
    assert central.indirect.import_leakage == D("168.46")
