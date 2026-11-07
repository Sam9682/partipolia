from decimal import Decimal

from app.economics.disclosure import (
    Cohort,
    NationalRow,
    RegionRow,
    complementary_suppression,
    primary_suppressed,
    publish_categories,
    publish_regions,
)
from hypothesis import given
from hypothesis import strategies as st


def _c(key: str, n: int) -> Cohort:
    return Cohort(key, n, Decimal(n))


def test_primary_includes_zero_and_below_threshold() -> None:
    assert primary_suppressed([_c("a", 0), _c("b", 19), _c("c", 20)]) == {"a", "b"}


def test_complementary_hides_smallest_published_then_smallest_code() -> None:
    cohorts = [_c("a", 5), _c("b", 30), _c("c", 30), _c("d", 40)]
    assert complementary_suppression(cohorts, total_participants=105, hidden_residual_count=0) == {
        "a",
        "b",
    }


def test_no_complementary_when_unassigned_masks_residual() -> None:
    cohorts = [_c("a", 5), _c("b", 30)]
    assert complementary_suppression(cohorts, total_participants=40, hidden_residual_count=5) == {
        "a"
    }


def test_small_national_or_inconsistent_withholds_everything() -> None:
    cohorts = [_c("a", 30), _c("b", 30)]
    assert complementary_suppression(cohorts, total_participants=10, hidden_residual_count=0) == {
        "a",
        "b",
    }
    assert complementary_suppression(cohorts, total_participants=50, hidden_residual_count=0) == {
        "a",
        "b",
    }


def test_publish_regions_has_no_figure_when_suppressed() -> None:
    rows = [
        RegionRow("11", "A", 5, Decimal(5)),
        RegionRow("24", "B", 30, Decimal(30)),
        RegionRow("27", "C", 50, Decimal(50)),
    ]
    views = publish_regions(rows, NationalRow(85))
    by = {v.code: v for v in views}
    assert by["11"].status == by["24"].status == "suppressed"
    assert by["11"].participants is None and by["11"].amount is None
    assert by["27"].status == "published" and by["27"].participants == 50


def test_publish_regions_without_national_withholds_all() -> None:
    views = publish_regions([RegionRow("11", "A", 99, Decimal(1))], None)
    assert views[0].status == "suppressed"


def test_categories_grouped_into_other() -> None:
    cells = [_c("x", 5), _c("y", 30), _c("z", 40), _c("w", 50)]
    out = publish_categories(cells)
    assert [c.key for c in out] == ["w", "z", "other"]
    assert out[-1].amount == Decimal(35) and out[-1].participants is None


@given(
    st.lists(st.integers(min_value=0, max_value=60), min_size=1, max_size=12),
    st.integers(min_value=0, max_value=30),
)
def test_residual_never_reveals_a_single_withheld_region(
    counts: list[int], unassigned: int
) -> None:
    cohorts = [_c(f"r{i:02d}", n) for i, n in enumerate(counts)]
    total = sum(counts) + unassigned
    withheld = complementary_suppression(
        cohorts, total_participants=total, hidden_residual_count=unassigned
    )
    assert primary_suppressed(cohorts) <= withheld
    if total >= 20:
        hidden_non_empty = [c for c in cohorts if c.key in withheld and c.participants > 0]
        components = len(hidden_non_empty) + (1 if unassigned > 0 else 0)
        published_left = [c for c in cohorts if c.key not in withheld]
        assert not (components == 1 and hidden_non_empty and published_left)
