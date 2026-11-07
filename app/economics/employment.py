"""Employment-induced fiscal impact (labour-related flows only).

The wage bill of a branch reacts to a value-added loss through a configurable
elasticity that depends on the horizon (short, medium, long). A turnover loss is
never assumed to cause a proportional job loss (elasticities are below 1)::

    wage-bill change = elasticity x (value-added loss / value added) x gross wages

Part of that change is a separation (``job_loss_share``) and part a reduction of
hours, overtime, bonuses or hiring (covered by partial activity where applicable).

Public finance effects, with labour-related taxes only (corporate and production
taxes are in the indirect module):

* revenue lost: employer contributions (branch-specific rate derived from INSEE
  compensation and wages), employee contributions, income tax;
* additional public expenditure: unemployment benefits for separations and
  partial-activity allowances for hours reductions.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.types import ModelSnapshot

HORIZONS = ("short", "medium", "long")
MONTHS = Decimal(12)


@dataclass(frozen=True, slots=True)
class EmploymentImpact:
    horizon: str
    wage_bill_change: Decimal
    employer_contributions: Decimal
    employee_contributions: Decimal
    income_tax: Decimal
    unemployment_benefits: Decimal
    partial_activity_cost: Decimal
    by_branch: Mapping[str, Decimal]  # wage-bill change by branch

    @property
    def revenue_loss(self) -> Decimal:
        return self.employer_contributions + self.employee_contributions + self.income_tax

    @property
    def additional_public_expenditure(self) -> Decimal:
        return self.unemployment_benefits + self.partial_activity_cost


def elasticity_for(snapshot: ModelSnapshot, horizon: str, branch: str, scenario: str) -> Decimal:
    """Branch-specific elasticity if defined, else the horizon default (``horizon:*``)."""
    table = snapshot.keyed("employment_elasticity")
    chosen = table.get(f"{horizon}:{branch}") or table[f"{horizon}:*"]
    return chosen.value(scenario)


def compute_employment_impact(
    snapshot: ModelSnapshot,
    value_added_change: Mapping[str, Decimal],
    scenario: str,
    horizon: str = "medium",
) -> EmploymentImpact:
    if horizon not in HORIZONS:
        raise ValueError(f"unknown horizon {horizon!r}")
    added = {k: v.value(scenario) for k, v in snapshot.keyed("branch_value_added").items()}
    wages = {k: v.value(scenario) for k, v in snapshot.keyed("branch_wages").items()}
    compensation = {k: v.value(scenario) for k, v in snapshot.keyed("branch_compensation").items()}

    employee_rate = snapshot.param("employee_contribution_rate").value(scenario)
    tax_rate = snapshot.param("income_tax_rate_on_wages").value(scenario)
    separation = snapshot.param("job_loss_share", horizon).value(scenario)
    replacement = snapshot.param("unemployment_replacement_rate").value(scenario)
    months = snapshot.param("unemployment_months", horizon).value(scenario)
    partial_rate = snapshot.param("partial_activity_cost_rate").value(scenario)

    with localcontext(engine_context()):
        employer = employee = income_tax = ZERO
        total_change = ZERO
        by_branch: dict[str, Decimal] = {}
        for branch, d_va in value_added_change.items():
            if not d_va or not added.get(branch) or not wages.get(branch):
                continue
            relative_loss = d_va / added[branch]
            change = (
                elasticity_for(snapshot, horizon, branch, scenario) * relative_loss * wages[branch]
            )
            employer_rate = (compensation[branch] - wages[branch]) / wages[branch]
            employer += change * employer_rate
            employee += change * employee_rate
            income_tax += change * tax_rate
            total_change += change
            by_branch[branch] = money(change)
        separated = total_change * separation
        hours = total_change - separated
        benefits = separated * replacement * months / MONTHS
        partial = hours * partial_rate
        return EmploymentImpact(
            horizon=horizon,
            wage_bill_change=money(total_change),
            employer_contributions=money(employer),
            employee_contributions=money(employee),
            income_tax=money(income_tax),
            unemployment_benefits=money(benefits),
            partial_activity_cost=money(partial),
            by_branch={k: v for k, v in by_branch.items() if v},
        )
