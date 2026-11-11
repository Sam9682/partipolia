"""Bilan « contributeur net / bénéficiaire net » (pur, sans I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.economics.decimal_utils import ZERO
from app.economics.net_balance.aids import (
    AidEstimate,
    MonetaryAid,
    compute_aid_totals,
    estimate_child_aids,
    estimate_income_aids,
)
from app.economics.net_balance.charges import StatusCharges, compute_charges
from app.economics.net_balance.consumption import (
    ConsumptionContribution,
    compute_consumption_contribution,
)
from app.economics.net_balance.income_tax import IncomeTaxResult, compute_income_tax
from app.economics.net_balance.non_reducible import (
    NonReducibleTaxes,
    compute_non_reducible_taxes,
)
from app.economics.net_balance.parameters import NetBalanceParameterRow, NetBalanceParameters
from app.economics.net_balance.real_estate import (
    PropertyTaxResult,
    RentalIncomeTaxResult,
    compute_property_tax,
    compute_rental_income_tax,
)
from app.economics.net_balance.types import Status
from app.economics.net_balance.validation import NetBalanceInput
from app.economics.types import Confidence, ModelSnapshot


class Verdict(StrEnum):
    CONTRIBUTEUR_NET = "contributeur_net"
    BENEFICIAIRE_NET = "beneficiaire_net"
    SOLDE_NUL = "solde_nul"


@dataclass(frozen=True, slots=True)
class NetBalanceResult:
    charges: tuple[StatusCharges, ...]
    employee_charges_total: Decimal  # hors fonctionnaire
    employer_charges_total: Decimal  # hors fonctionnaire
    consumption: ConsumptionContribution
    contributions_total: Decimal
    monetary_aids: Decimal
    non_monetary_aids: Decimal
    aids_total: Decimal
    balance: Decimal  # contributions_total - aids_total
    verdict: Verdict
    state_expense_total: Decimal  # rémunération estimée des fonctionnaires
    child_aid_estimates: Mapping[MonetaryAid, AidEstimate]
    provisional_parameters: tuple[str, ...]  # identifiants triés, sans doublon
    income_tax: IncomeTaxResult
    property_tax: PropertyTaxResult
    rental_income_tax: RentalIncomeTaxResult
    non_reducible: NonReducibleTaxes


def classify(balance: Decimal) -> Verdict:
    """Verdict déterminé par le signe du bilan."""
    if balance > ZERO:
        return Verdict.CONTRIBUTEUR_NET
    if balance < ZERO:
        return Verdict.BENEFICIAIRE_NET
    return Verdict.SOLDE_NUL


def _used_rows(
    charges: tuple[StatusCharges, ...],
    children: int,
    params: NetBalanceParameters,
    income_tax: IncomeTaxResult,
    property_tax: PropertyTaxResult,
    non_reducible: NonReducibleTaxes,
) -> list[NetBalanceParameterRow]:
    """Lignes de paramètres réellement utilisées par le calcul."""
    rows: list[NetBalanceParameterRow] = []
    for line in charges:
        rates = params.rates[line.status]
        rows.extend((rates.employee_row, rates.employer_row))
    scale = params.family_scale
    rows.append(scale.allocations_two_children_row)
    if children > 2:
        rows.append(scale.allocations_extra_child_row)
    rows.extend(
        (scale.supplement_min_children_row, scale.supplement_amount_row, scale.ars_per_child_row)
    )
    rows.extend(income_tax.used_rows)
    if property_tax.used_default:
        rows.append(params.property_tax.default_rate_row)
    rows.extend(params.income_aids.housing_rows)
    rows.extend(params.income_aids.rsa_rows)
    rows.extend(non_reducible.used_rows)
    return rows


def compute_net_balance(
    data: NetBalanceInput, params: NetBalanceParameters, snapshot: ModelSnapshot
) -> NetBalanceResult:
    """Bilan = contributions (charges hors fonctionnaire + consommation) − aides."""
    charges = tuple(compute_charges(line, params) for line in data.incomes)
    contributing = [c for c in charges if c.status != Status.FONCTIONNAIRE]
    employee_total = sum((c.employee_charges for c in contributing), ZERO)
    employer_total = sum((c.employer_charges for c in contributing), ZERO)
    state_expense = sum(
        (
            c.net_income + c.employee_charges + c.employer_charges
            for c in charges
            if c.state_expense
        ),
        ZERO,
    )

    consumption = compute_consumption_contribution(
        snapshot, data.reductions, data.adults, data.reference_overrides
    )
    net_income = sum((line.net_income for line in data.incomes), ZERO)
    income_tax = compute_income_tax(
        net_income, data.property_income, data.adults, data.children, params.income_tax
    )
    property_tax = compute_property_tax(
        data.property_income, data.property_tax_rate, params.property_tax
    )
    rental_income_tax = compute_rental_income_tax(data.property_income, income_tax.average_rate)
    non_reducible = compute_non_reducible_taxes(data.non_reducible, params.non_reducible, snapshot)
    contributions = (
        employee_total
        + employer_total
        + consumption.total
        + non_reducible.total
        + income_tax.tax
        + property_tax.tax
        + rental_income_tax.tax
    )

    totals = compute_aid_totals(
        data.monetary_aids, (a.annual_value for a in data.non_monetary_aids)
    )
    balance = contributions - totals.total

    provisional_ids = {
        r.id
        for r in _used_rows(charges, data.children, params, income_tax, property_tax, non_reducible)
        if r.confidence == Confidence.PROVISIONAL
    }
    provisional_ids.update(
        name
        for name in non_reducible.used_fuel_parameters
        if snapshot.param(name).confidence == Confidence.PROVISIONAL
    )
    provisional = tuple(sorted(provisional_ids))

    aid_estimates = {
        **estimate_child_aids(data.children, params.family_scale),
        **estimate_income_aids(
            net_income, data.property_income, data.adults, data.children, params.income_aids
        ),
    }

    return NetBalanceResult(
        charges=charges,
        employee_charges_total=employee_total,
        employer_charges_total=employer_total,
        consumption=consumption,
        contributions_total=contributions,
        monetary_aids=totals.monetary,
        non_monetary_aids=totals.non_monetary,
        aids_total=totals.total,
        balance=balance,
        verdict=classify(balance),
        state_expense_total=state_expense,
        child_aid_estimates=aid_estimates,
        provisional_parameters=provisional,
        income_tax=income_tax,
        property_tax=property_tax,
        rental_income_tax=rental_income_tax,
        non_reducible=non_reducible,
    )
