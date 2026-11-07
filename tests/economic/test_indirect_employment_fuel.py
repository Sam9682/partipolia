"""Indirect (Leontief), employment and fuel modules against hand-computed values."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.employment import compute_employment_impact
from app.economics.fuel import compute_fuel_impact
from app.economics.input_output import compute_indirect_impact, invert

from tests.economic.helpers import row, snapshot_from_csv, three_sector_io

D = Decimal


# --------------------------------------------------------------------------- Leontief
def test_matrix_inverse_of_known_matrix() -> None:
    inv = invert([[D("2"), D("1")], [D("1"), D("1")]])
    assert inv == [[D("1"), D("-1")], [D("-1"), D("2")]]


def test_singular_matrix_is_rejected() -> None:
    with pytest.raises(ValueError):
        invert([[D("1"), D("2")], [D("2"), D("4")]])


def test_three_sector_hand_computed() -> None:
    # x_A = 0.2*x_B + 100, x_B = 0.1*x_A, x_C = 0.05*x_A  =>  x_A = 100 / 0.98
    snapshot = snapshot_from_csv(three_sector_io())
    result = compute_indirect_impact(snapshot, {"restaurants": D("100")}, "central")
    assert result.output_change["A"] == D("102.04")
    assert result.output_change["B"] == D("10.20")
    assert result.output_change["C"] == D("5.10")
    assert result.turnover_change == D("117.35")
    # value added: 0.5 x 102.0408 + 0.4 x 10.2041 + 0.4 x 5.1020 = 57.142857
    assert result.value_added_total == D("57.14")
    assert result.production_tax == D("2.29")  # 4 % of value added
    assert result.corporate_tax == D("3.57")  # 57.14 x 0.5 (EBE/VA) x 0.5 x 25 %
    assert result.import_leakage == D("0.00")


def test_imports_leak_out_of_the_domestic_chain() -> None:
    domestic = compute_indirect_impact(
        snapshot_from_csv(three_sector_io()), {"restaurants": D("100")}, "central"
    )
    leaky = compute_indirect_impact(
        snapshot_from_csv(three_sector_io(import_share_a="0.25")),
        {"restaurants": D("100")},
        "central",
    )
    assert leaky.import_leakage == D("25.00")
    assert leaky.final_demand["A"] == D("75.00")
    assert leaky.output_change["A"] == D("76.53")  # 75 / 0.98
    assert leaky.value_added_total == pytest.approx(
        domestic.value_added_total * D("0.75"), abs=D("0.02")
    )


def test_zero_spending_gives_zero_impact() -> None:
    result = compute_indirect_impact(
        snapshot_from_csv(three_sector_io()), {"restaurants": D("0")}, "central"
    )
    assert result.turnover_change == D("0.00") and result.tax_revenue_loss == D("0.00")


def test_indirect_result_contains_no_vat_or_labour_taxes() -> None:
    fields = set(compute_indirect_impact.__annotations__) | {
        "production_tax",
        "corporate_tax",
    }
    from app.economics.input_output import IndirectImpact

    assert not {n for n in IndirectImpact.__slots__ if "vat" in n.lower() or "contribution" in n}
    assert "production_tax" in fields


def test_unknown_branch_in_demand_share_is_rejected() -> None:
    text = three_sector_io().replace("restaurants,demand_share,A", "restaurants,demand_share,ZZ")
    with pytest.raises(KeyError):
        compute_indirect_impact(snapshot_from_csv(text), {"restaurants": D("1")}, "central")


# --------------------------------------------------------------------------- employment
def _employment_csv() -> str:
    text = three_sector_io()
    for branch, wages, comp in (("A", 30, 42), ("B", 50, 60), ("C", 10, 12)):
        text += row("branch_wages", branch, wages) + row("branch_compensation", branch, comp)
    text += row("employment_elasticity", "medium:*", "0.5")
    text += row("employment_elasticity", "short:*", "0.2")
    text += row("employment_elasticity", "long:*", "0.8")
    for horizon, share in (("short", "0.1"), ("medium", "0.4"), ("long", "0.7")):
        text += row("job_loss_share", horizon, share)
    for horizon, months in (("short", 2), ("medium", 6), ("long", 10)):
        text += row("unemployment_months", horizon, months)
    text += row("employee_contribution_rate", "*", "0.2")
    text += row("income_tax_rate_on_wages", "*", "0.05")
    text += row("unemployment_replacement_rate", "*", "0.5")
    text += row("partial_activity_cost_rate", "*", "0.3")
    return text


def _value_added_change(snapshot):
    return compute_indirect_impact(snapshot, {"restaurants": D("10")}, "central").value_added_exact


def test_employment_hand_computed() -> None:
    snapshot = snapshot_from_csv(_employment_csv())
    result = compute_employment_impact(snapshot, _value_added_change(snapshot), "central", "medium")
    # branch wage-bill changes: A 1.5306, B 0.1276, C 0.0510  => 1.7092
    assert result.wage_bill_change == D("1.71")
    assert result.employer_contributions == D("0.65")  # A at 40 %, B and C at 20 %
    assert result.employee_contributions == D("0.34")
    assert result.income_tax == D("0.09")
    assert result.unemployment_benefits == D("0.17")  # 40 % separations x 50 % x 6/12
    assert result.partial_activity_cost == D("0.31")  # 60 % hours x 30 %
    assert result.revenue_loss == D("1.08")
    assert result.additional_public_expenditure == D("0.48")


def test_job_loss_is_not_proportional_to_turnover_loss() -> None:
    snapshot = snapshot_from_csv(_employment_csv())
    d_va = _value_added_change(snapshot)
    result = compute_employment_impact(snapshot, d_va, "central", "long")
    wages = {"A": D(30), "B": D(50), "C": D(10)}
    added = {"A": D(50), "B": D(80), "C": D(20)}
    proportional = sum(d_va[b] / added[b] * wages[b] for b in wages)
    assert result.wage_bill_change < proportional  # elasticity 0.8 < 1


def test_horizons_are_ordered() -> None:
    snapshot = snapshot_from_csv(_employment_csv())
    d_va = _value_added_change(snapshot)
    changes = [
        compute_employment_impact(snapshot, d_va, "central", h).wage_bill_change
        for h in ("short", "medium", "long")
    ]
    assert changes[0] < changes[1] < changes[2]


def test_unknown_horizon_is_rejected() -> None:
    snapshot = snapshot_from_csv(_employment_csv())
    with pytest.raises(ValueError):
        compute_employment_impact(snapshot, {}, "central", "eternal")


def test_branch_specific_elasticity_overrides_default() -> None:
    text = _employment_csv() + row("employment_elasticity", "medium:A", "0.1")
    snapshot = snapshot_from_csv(text)
    base = compute_employment_impact(
        snapshot_from_csv(_employment_csv()), _value_added_change(snapshot), "central"
    )
    lower = compute_employment_impact(snapshot, _value_added_change(snapshot), "central")
    assert lower.wage_bill_change < base.wage_bill_change


# --------------------------------------------------------------------------- fuel
def _fuel_csv() -> str:
    from tests.economic.helpers import CSV_HEADER

    text = CSV_HEADER
    for ptype, value in (
        ("fuel_l_per_100km", "6.5"),
        ("fuel_price_ttc_per_l", "1.75"),
        ("fuel_excise_per_l", "0.60"),
        ("fuel_vat_rate", "0.20"),
    ):
        text += row(ptype, "*", value)
    text += row("trips_per_1000_eur", "*", 40, category="restaurants")
    text += row("km_per_trip", "*", 15, category="restaurants")
    text += row("car_share", "*", "0.6", category="restaurants")
    return text


def test_fuel_hand_computed() -> None:
    # 1000 EUR avoided -> 40 trips x 15 km x 0.6 = 360 km -> 23.4 l -> 40.95 EUR at the pump
    result = compute_fuel_impact(
        snapshot_from_csv(_fuel_csv()), {"restaurants": D("1000")}, "central"
    )
    assert result.litres == D("23.400")
    assert result.household_fuel_savings == D("40.95")
    assert result.excise == D("14.04")
    assert result.vat == D("6.82")  # 40.95 x 0.2 / 1.2 = 6.825 -> half-even
    assert result.tax_revenue_loss == D("20.86")


def test_fuel_is_zero_without_avoided_spending() -> None:
    result = compute_fuel_impact(snapshot_from_csv(_fuel_csv()), {"restaurants": D("0")}, "central")
    assert result.litres == D("0.000") and result.tax_revenue_loss == D("0.00")
