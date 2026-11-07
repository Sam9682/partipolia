from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.aggregate import aggregate_savings, apportion
from app.economics.decimal_utils import money
from app.services import aggregation_service
from hypothesis import given
from hypothesis import strategies as st

D = Decimal


def test_aggregate_savings_is_reexported() -> None:
    assert aggregation_service.aggregate_savings is aggregate_savings


def test_apportion_largest_remainder() -> None:
    result = apportion(D("1.00"), {"a": D(1) / 3, "b": D(1) / 3, "c": D(1) / 3})
    assert sum(result.values()) == D("1.00")
    assert sorted(result.values()) == [D("0.33"), D("0.33"), D("0.34")]


def test_apportion_empty_and_zero() -> None:
    assert apportion(D(0), {}) == {}
    assert apportion(D(0), {"a": D(0)}) == {"a": D("0.00")}


def test_apportion_rejects_negative_and_inconsistent() -> None:
    with pytest.raises(ValueError):
        apportion(D(0), {"a": D(-1), "b": D(1)})
    with pytest.raises(ValueError):
        apportion(D(10), {"a": D(3), "b": D(3)})


@given(
    st.lists(
        st.decimals(min_value=0, max_value=10**9, places=6, allow_nan=False, allow_infinity=False),
        min_size=1,
        max_size=12,
    )
)
def test_apportion_sums_exactly(values: list[Decimal]) -> None:
    """**Validates: Requirements 4.4**"""
    parts = dict(enumerate(values))
    total = sum(values, D(0))
    result = apportion(total, parts)
    assert sum(result.values()) == money(total)
    assert all(v >= 0 and v == v.quantize(D("0.01")) for v in result.values())
