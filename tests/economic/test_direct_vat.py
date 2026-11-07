"""Direct VAT: standard / reduced / multiple rates, TTC<->HT, determinism."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.defaults import bootstrap_snapshot
from app.economics.direct import (
    category_direct_impact,
    compute_direct_impact,
    vat_from_ttc,
)
from app.economics.personal import compute_personal_savings
from app.economics.types import Confidence, VatComponent, VatProfile

D = Decimal


def _profile(*pairs: tuple[str, str]) -> VatProfile:
    split = tuple(VatComponent(D(r), D(s)) for r, s in pairs)
    return VatProfile({"low": split, "central": split, "high": split}, Confidence.HIGH, "test")


def test_standard_vat_from_ttc() -> None:
    # 120 TTC at 20 % -> 100 HT + 20 VAT
    assert vat_from_ttc(D("120"), D("0.20")) == D("20")


def test_reduced_vat_from_ttc() -> None:
    # 110 TTC at 10 % -> 100 HT + 10 VAT
    assert vat_from_ttc(D("110"), D("0.10")) == D("10")
    # 105.50 TTC at 5.5 % -> 100 HT + 5.50 VAT
    assert vat_from_ttc(D("105.50"), D("0.055")) == D("5.5")


def test_ttc_and_ht_are_consistent() -> None:
    impact = category_direct_impact("x", D("1000.00"), _profile(("0.20", "1")), "central")
    assert impact.vat == D("166.67")
    assert impact.turnover_ht == D("833.33")
    assert impact.vat + impact.turnover_ht == impact.avoided_ttc


def test_multiple_vat_rates_in_one_category() -> None:
    profile = _profile(("0.10", "0.5"), ("0.20", "0.5"))
    impact = category_direct_impact("x", D("1200.00"), profile, "central")
    # 600 @10 % -> 54.545454..., 600 @20 % -> 100.00 ; total 154.55
    assert impact.vat == D("154.55")
    assert [line.rate for line in impact.lines] == [D("0.10"), D("0.20")]
    assert sum(line.ttc for line in impact.lines) == D("1200.00")
    assert sum(line.vat for line in impact.lines) == impact.vat


def test_zero_expenditure_gives_zero_vat() -> None:
    impact = category_direct_impact("x", D("0.00"), _profile(("0.20", "1")), "central")
    assert impact.vat == D("0.00") and impact.turnover_ht == D("0.00")


def test_full_reduction_spec_categories_with_bootstrap() -> None:
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, {c.code: D("100") for c in snapshot.categories})
    impact = compute_direct_impact(snapshot, savings)
    assert impact.avoided_ttc == D("4170.00")
    assert impact.vat + impact.turnover_ht == impact.avoided_ttc
    clothing = next(c for c in impact.categories if c.code == "clothing")
    assert clothing.vat == D("78.33")  # 470 x 0.2 / 1.2 = 78.333...


def test_vat_scenarios_are_ordered() -> None:
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, {c.code: D("100") for c in snapshot.categories})
    low, central, high = (
        compute_direct_impact(snapshot, savings, s).vat for s in ("low", "central", "high")
    )
    assert low <= central <= high
    assert low < high


def test_zero_reduction_gives_zero_impact() -> None:
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, {})
    assert compute_direct_impact(snapshot, savings).vat == D("0.00")


def test_deterministic() -> None:
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, {"restaurants": D("37.5")})
    assert compute_direct_impact(snapshot, savings) == compute_direct_impact(snapshot, savings)


def test_invalid_vat_profiles_rejected() -> None:
    with pytest.raises(ValueError):
        _profile(("0.20", "0.5"))  # shares do not sum to 1
    with pytest.raises(ValueError):
        _profile(("1.20", "1"))  # rate is not a fraction below 1
    with pytest.raises(ValueError):
        VatProfile({"central": (VatComponent(D("0.2"), D("1")),)}, Confidence.HIGH, "t")
