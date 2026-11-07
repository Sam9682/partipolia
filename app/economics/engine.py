"""Fiscal impact engine: orchestrates direct, indirect, induced and fuel effects.

Sign convention (reported as positive numbers):

* revenue losses: ``direct`` (VAT on final goods), ``fuel`` (excise and VAT on
  avoided fuel), ``indirect`` (production tax, corporate tax along the supplier
  chain), ``induced`` (labour-related revenue);
* ``additional_public_expenditure``: unemployment benefits and partial activity;
* ``rebound_offset``: VAT recovered when households re-spend part of their savings.

    gross revenue loss = direct + fuel + indirect + induced
    net public impact  = gross revenue loss + additional expenditure - rebound offset

Distinct indicators, never to be confused: personal savings, reduced commercial
turnover, reduced value added, reduced tax revenue, additional public expenditure.

Each figure is computed for the low, central and high scenarios from the
snapshot's parameter ranges. Optional modules are skipped (and reported as such)
when the snapshot lacks their data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.direct import DirectImpact, compute_direct_impact, vat_from_ttc
from app.economics.employment import EmploymentImpact, compute_employment_impact
from app.economics.fuel import FuelImpact, compute_fuel_impact
from app.economics.input_output import IndirectImpact, compute_indirect_impact
from app.economics.parameters import available_modules
from app.economics.types import SCENARIOS, ModelSnapshot, PersonalSavings

#: Simplified attribution of each component to a public sub-sector (documented
#: approximation: shares of VAT and excise assigned to local government and social
#: security are ignored).
COMPONENT_SECTOR: Mapping[str, str] = {
    "direct_vat": "central",
    "fuel_excise": "central",
    "fuel_vat": "central",
    "production_tax": "local",
    "corporate_tax": "central",
    "employer_contributions": "social_security",
    "employee_contributions": "social_security",
    "income_tax": "central",
    "unemployment_benefits": "social_security",
    "partial_activity_cost": "social_security",
}


@dataclass(frozen=True, slots=True)
class ScenarioOutcome:
    scenario: str
    direct: DirectImpact
    fuel: FuelImpact | None
    indirect: IndirectImpact | None
    employment: EmploymentImpact | None
    rebound_offset: Decimal
    direct_total: Decimal  # VAT + fuel taxes
    indirect_total: Decimal
    induced_total: Decimal
    gross_revenue_loss: Decimal
    additional_public_expenditure: Decimal
    net_public_impact: Decimal
    components: Mapping[str, Decimal] = field(default_factory=dict)

    def by_public_sector(self) -> dict[str, Decimal]:
        """Revenue losses and extra spending per public sub-sector (simplified)."""
        sectors: dict[str, Decimal] = {"central": ZERO, "local": ZERO, "social_security": ZERO}
        for name, amount in self.components.items():
            sectors[COMPONENT_SECTOR[name]] += amount
        return sectors


@dataclass(frozen=True, slots=True)
class EngineOutcome:
    model_version: str
    dataset_versions: tuple[str, ...]
    horizon: str
    modules: Mapping[str, bool]
    low: ScenarioOutcome
    central: ScenarioOutcome
    high: ScenarioOutcome

    def scenario(self, name: str) -> ScenarioOutcome:
        return {"low": self.low, "central": self.central, "high": self.high}[name]


def run_scenario(
    snapshot: ModelSnapshot,
    savings: PersonalSavings,
    scenario: str,
    horizon: str = "medium",
    modules: Mapping[str, bool] | None = None,
) -> ScenarioOutcome:
    enabled = modules if modules is not None else available_modules(snapshot)
    direct = compute_direct_impact(snapshot, savings, scenario)
    avoided = {c.code: c.avoided_ttc for c in direct.categories}
    turnover = {c.code: c.turnover_ht for c in direct.categories}

    fuel = compute_fuel_impact(snapshot, avoided, scenario) if enabled["fuel"] else None
    indirect = (
        compute_indirect_impact(snapshot, turnover, scenario) if enabled["indirect"] else None
    )
    employment = (
        compute_employment_impact(snapshot, indirect.value_added_exact, scenario, horizon)
        if enabled["induced"] and indirect is not None
        else None
    )

    components: dict[str, Decimal] = {"direct_vat": direct.vat}
    if fuel:
        components["fuel_excise"] = fuel.excise
        components["fuel_vat"] = fuel.vat
    if indirect:
        components["production_tax"] = indirect.production_tax
        components["corporate_tax"] = indirect.corporate_tax
    if employment:
        components["employer_contributions"] = employment.employer_contributions
        components["employee_contributions"] = employment.employee_contributions
        components["income_tax"] = employment.income_tax
        components["unemployment_benefits"] = employment.unemployment_benefits
        components["partial_activity_cost"] = employment.partial_activity_cost

    with localcontext(engine_context()):
        rebound = ZERO
        if enabled["rebound"]:
            share = snapshot.param("respend_share").value(scenario)
            rate = snapshot.param("respend_vat_rate").value(scenario)
            rebound = vat_from_ttc(direct.avoided_ttc * share, rate)
        direct_total = direct.vat + (fuel.tax_revenue_loss if fuel else ZERO)
        indirect_total = indirect.tax_revenue_loss if indirect else ZERO
        induced_total = employment.revenue_loss if employment else ZERO
        extra = employment.additional_public_expenditure if employment else ZERO
        gross = direct_total + indirect_total + induced_total
        return ScenarioOutcome(
            scenario=scenario,
            direct=direct,
            fuel=fuel,
            indirect=indirect,
            employment=employment,
            rebound_offset=money(rebound),
            direct_total=money(direct_total),
            indirect_total=money(indirect_total),
            induced_total=money(induced_total),
            gross_revenue_loss=money(gross),
            additional_public_expenditure=money(extra),
            net_public_impact=money(gross + extra - rebound),
            components=components,
        )


def run_engine(
    snapshot: ModelSnapshot, savings: PersonalSavings, horizon: str = "medium"
) -> EngineOutcome:
    modules = available_modules(snapshot)
    outcomes = {s: run_scenario(snapshot, savings, s, horizon, modules) for s in SCENARIOS}
    return EngineOutcome(
        model_version=snapshot.model_version,
        dataset_versions=snapshot.dataset_versions,
        horizon=horizon,
        modules=modules,
        low=outcomes["low"],
        central=outcomes["central"],
        high=outcomes["high"],
    )
