"""Personal savings: Decimal arithmetic, edge cases, specification example."""

from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

import pytest
from app.economics.categories import (
    CATEGORY_CODES,
    DEFAULT_CATEGORIES,
    TOTAL_REFERENCE_EXPENDITURE,
)
from app.economics.decimal_utils import money, pct_to_fraction, to_decimal
from app.economics.defaults import bootstrap_snapshot
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


def test_reference_total_is_4170() -> None:
    assert D("4170") == TOTAL_REFERENCE_EXPENDITURE
    assert len(DEFAULT_CATEGORIES) == 6
    assert len(set(CATEGORY_CODES)) == 6


def test_spec_example_savings() -> None:
    # 1500 + 235 + 450 + 350 + 180 + 300 = 3015
    result = compute_personal_savings(bootstrap_snapshot(), SPEC_EXAMPLE)
    assert result.annual_savings == D("3015.00")
    assert result.monthly_savings == D("251.25")
    assert result.original_expenditure == D("4170.00")
    assert result.remaining_expenditure == D("1155.00")
    by_code = {c.code: c for c in result.categories}
    assert by_code["clothing"].avoided_expenditure == D("235.00")
    assert by_code["clothing"].remaining_expenditure == D("235.00")


def test_zero_reduction_gives_zero_savings() -> None:
    result = compute_personal_savings(bootstrap_snapshot(), {})
    assert result.annual_savings == D("0.00")
    assert result.monthly_savings == D("0.00")
    assert result.remaining_expenditure == D("4170.00")


def test_full_reduction_saves_everything() -> None:
    full = {code: D("100") for code in CATEGORY_CODES}
    result = compute_personal_savings(bootstrap_snapshot(), full)
    assert result.annual_savings == D("4170.00")
    assert result.remaining_expenditure == D("0.00")
    assert result.monthly_savings == D("347.50")


def test_household_scales_with_adults() -> None:
    single = compute_personal_savings(bootstrap_snapshot(), SPEC_EXAMPLE, adults=1)
    couple = compute_personal_savings(bootstrap_snapshot(), SPEC_EXAMPLE, adults=2)
    assert couple.annual_savings == single.annual_savings * 2


def test_deterministic_for_identical_inputs() -> None:
    a = compute_personal_savings(bootstrap_snapshot(), SPEC_EXAMPLE)
    b = compute_personal_savings(bootstrap_snapshot(), dict(reversed(list(SPEC_EXAMPLE.items()))))
    assert a == b


def test_fractional_percentages_are_exact() -> None:
    # 33.33 % of 1500 = 499.95 exactly (no float drift)
    result = compute_personal_savings(bootstrap_snapshot(), {"restaurants": D("33.33")})
    assert result.annual_savings == D("499.95")


@pytest.mark.parametrize("bad", [D("-1"), D("100.01")])
def test_out_of_range_percentage_rejected(bad: Decimal) -> None:
    with pytest.raises(ValueError):
        compute_personal_savings(bootstrap_snapshot(), {"restaurants": bad})


def test_unknown_category_rejected() -> None:
    with pytest.raises(KeyError):
        compute_personal_savings(bootstrap_snapshot(), {"yachts": D("10")})


@pytest.mark.parametrize("adults", [0, 11])
def test_adults_bounds(adults: int) -> None:
    with pytest.raises(ValueError):
        compute_personal_savings(bootstrap_snapshot(), {}, adults=adults)


def test_money_uses_bankers_rounding() -> None:
    assert money(D("0.125")) == D("0.12")
    assert money(D("0.135")) == D("0.14")


def test_floats_are_rejected() -> None:
    with pytest.raises(TypeError):
        to_decimal(0.1)  # type: ignore[arg-type]
    assert pct_to_fraction(D("50")) == D("0.5")


def test_engine_package_has_no_web_or_db_imports() -> None:
    forbidden = (
        "fastapi",
        "sqlalchemy",
        "starlette",
        "app.api",
        "app.web",
        "app.models",
        "app.services",
    )
    root = Path(__file__).resolve().parents[2] / "app" / "economics"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                assert not name.startswith(forbidden), f"{path.name} imports {name}"
