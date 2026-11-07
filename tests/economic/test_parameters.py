"""Parameter rows, CSV parsing and snapshot construction (pure)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.csv_import import parse_parameter_csv
from app.economics.defaults import bootstrap_snapshot
from app.economics.direct import compute_direct_impact
from app.economics.parameters import build_snapshot, missing_parameters
from app.economics.personal import compute_personal_savings
from app.economics.types import CategoryParameters

from tests.economic.helpers import CSV_HEADER, bootstrap_vat_csv

D = Decimal


def _categories() -> list[CategoryParameters]:
    return list(bootstrap_snapshot().categories)


def test_bootstrap_csv_roundtrip_gives_identical_results() -> None:
    parsed = parse_parameter_csv(bootstrap_vat_csv())
    assert parsed.ok, parsed.errors
    rebuilt = build_snapshot("v-test", ["ds@v1"], _categories(), parsed.rows)
    original = bootstrap_snapshot()
    reductions = {c.code: D("60") for c in original.categories}
    for scenario in ("low", "central", "high"):
        a = compute_direct_impact(
            original, compute_personal_savings(original, reductions), scenario
        )
        b = compute_direct_impact(rebuilt, compute_personal_savings(rebuilt, reductions), scenario)
        assert a.vat == b.vat


def test_missing_columns_are_reported() -> None:
    result = parse_parameter_csv("category_code,key\nx,y\n")
    assert not result.ok and "missing columns" in result.errors[0]


@pytest.mark.parametrize(
    "line,fragment",
    [
        ("restaurants,vat_split,0.2,abc,1,1,share,high,", "not a number"),
        ("restaurants,vat_split,0.2,NaN,1,1,share,high,", "not a finite number"),
        ("restaurants,vat_split,0.2,1,1,1,share,certain,", "line 2"),
        ("restaurants,unknown_type,0.2,1,1,1,share,high,", "unknown parameter type"),
        (",vat_split,0.2,1,1,1,share,high,", "category_code is required"),
        ("restaurants,vat_split,1.5,1,1,1,share,high,", "VAT rate must be in"),
        ("restaurants,vat_split,0.2,2,2,2,share,high,", "share must be in"),
    ],
)
def test_invalid_rows_are_rejected(line: str, fragment: str) -> None:
    result = parse_parameter_csv(CSV_HEADER + line + "\n")
    assert not result.ok
    assert any(fragment in e for e in result.errors), result.errors


def test_vat_shares_must_sum_to_one() -> None:
    csv_text = CSV_HEADER + "restaurants,vat_split,0.10,0.5,0.5,0.5,share,high,\n"
    result = parse_parameter_csv(csv_text)
    assert any("expected 1" in e for e in result.errors)


def test_duplicate_parameters_are_rejected() -> None:
    line = "clothing,vat_split,0.20,1,1,1,share,high,\n"
    result = parse_parameter_csv(CSV_HEADER + line + line)
    assert any("duplicate" in e for e in result.errors)


def test_comma_decimal_separator_is_accepted() -> None:
    line = 'clothing,vat_split,"0,20",1,1,1,share,high,\n'
    assert parse_parameter_csv(CSV_HEADER + line).ok


def test_missing_parameters_lists_uncovered_categories() -> None:
    parsed = parse_parameter_csv(CSV_HEADER + "clothing,vat_split,0.20,1,1,1,share,high,\n")
    missing = missing_parameters(_categories(), parsed.rows)
    assert "vat_split for restaurants" in missing and "vat_split for clothing" not in missing


def test_build_snapshot_rejects_unknown_category() -> None:
    parsed = parse_parameter_csv(CSV_HEADER + "yachts,vat_split,0.20,1,1,1,share,high,\n")
    with pytest.raises(ValueError):
        build_snapshot("v", [], _categories(), parsed.rows)


def test_lowest_confidence_is_kept() -> None:
    csv_text = (
        CSV_HEADER
        + "restaurants,vat_split,0.10,0.8,0.8,0.8,share,high,\n"
        + "restaurants,vat_split,0.20,0.2,0.2,0.2,share,provisional,assumption\n"
    )
    snapshot = build_snapshot("v", [], _categories(), parse_parameter_csv(csv_text).rows)
    assert snapshot.vat_profiles["restaurants"].confidence.value == "provisional"
