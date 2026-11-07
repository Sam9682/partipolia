"""Shared helpers for economic tests."""

from __future__ import annotations

from app.economics.defaults import bootstrap_snapshot

CSV_HEADER = "category_code,parameter_type,key,low,central,high,unit,confidence,note\n"


def bootstrap_vat_csv(scale_restaurant_alcohol: bool = False) -> str:
    """Export the bootstrap VAT profiles as a parameter CSV.

    With ``scale_restaurant_alcohol`` the restaurant split is altered, to build a
    "next version" dataset in reproducibility tests.
    """
    snapshot = bootstrap_snapshot()
    lines: list[str] = []
    for code, profile in snapshot.vat_profiles.items():
        rates = sorted({c.rate for comps in profile.splits.values() for c in comps})
        for rate in rates:

            def share(scenario: str, rate=rate, profile=profile) -> str:
                for comp in profile.splits[scenario]:
                    if comp.rate == rate:
                        return str(comp.share)
                return "0"

            values = [share("low"), share("central"), share("high")]
            if scale_restaurant_alcohol and code == "restaurants":
                # 50/50 split between the 10 % and 20 % rates in every scenario
                values = ["0.5", "0.5", "0.5"]
            lines.append(
                f"{code},vat_split,{rate},{values[0]},{values[1]},{values[2]},share,"
                f"{profile.confidence.value},test fixture"
            )
    return CSV_HEADER + "\n".join(lines) + "\n"


def snapshot_from_csv(text: str, version: str = "test"):
    """Build a snapshot with the six default categories from CSV text."""
    from app.economics.csv_import import parse_parameter_csv
    from app.economics.defaults import default_categories
    from app.economics.parameters import build_snapshot

    parsed = parse_parameter_csv(text)
    assert parsed.ok, parsed.errors
    return build_snapshot(version, ["test@v1"], default_categories(), parsed.rows)


def row(
    ptype: str, key: str, low, central=None, high=None, category: str = "", conf: str = "high"
) -> str:
    central = low if central is None else central
    high = central if high is None else high
    return f"{category},{ptype},{key},{low},{central},{high},u,{conf},test\n"


def three_sector_io(import_share_a: str = "0") -> str:
    """Hand-checkable economy: A (output 100), B (200), C (50).

    A[i][j]: B buys 40 from A (0.2), A buys 10 from B (0.1), A buys 5 from C (0.05).
    """
    text = CSV_HEADER
    for branch, output, va, ebe in (("A", 100, 50, 25), ("B", 200, 80, 40), ("C", 50, 20, 10)):
        text += row("io_output", branch, output)
        text += row("branch_value_added", branch, va)
        text += row("branch_ebe", branch, ebe)
        text += row("io_import_share", branch, import_share_a if branch == "A" else "0")
    text += row("io_flow", "A>B", 40) + row("io_flow", "B>A", 10) + row("io_flow", "C>A", 5)
    text += row("demand_share", "A", 1, category="restaurants")
    text += row("production_tax_rate_on_va", "*", "0.04")
    text += row("ebe_to_taxable_profit", "*", "0.5")
    text += row("corporate_tax_rate", "*", "0.25")
    return text
