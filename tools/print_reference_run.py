"""Print the reference run of the engine (developer tool).

python tools/print_reference_run.py
"""

from __future__ import annotations

from decimal import Decimal

from app.economics.defaults import bootstrap_snapshot
from app.economics.engine import run_engine
from app.economics.personal import compute_personal_savings

EXAMPLE = {
    "restaurants": Decimal("100"),
    "clothing": Decimal("50"),
    "leisure": Decimal("75"),
    "hotels_holidays": Decimal("50"),
    "electronics": Decimal("30"),
    "digital_subscriptions": Decimal("100"),
}


def main() -> None:
    snapshot = bootstrap_snapshot()
    savings = compute_personal_savings(snapshot, EXAMPLE)
    outcome = run_engine(snapshot, savings)
    print("model", outcome.model_version, "modules", dict(outcome.modules))
    print("personal savings", savings.annual_savings)
    for name in ("low", "central", "high"):
        s = outcome.scenario(name)
        print(f"-- {name}")
        for key, value in s.components.items():
            print(f"   {key:26s} {value}")
        print(
            f"   direct={s.direct_total} indirect={s.indirect_total} induced={s.induced_total}"
            f" extra={s.additional_public_expenditure} rebound={s.rebound_offset}"
            f" net={s.net_public_impact}"
        )
        if s.indirect:
            print(
                f"   turnover_change={s.indirect.turnover_change}"
                f" value_added={s.indirect.value_added_total}"
                f" leakage={s.indirect.import_leakage}"
            )


if __name__ == "__main__":
    main()
