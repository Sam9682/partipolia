"""Charges salariales et patronales par statut (pur, taux moyen sur le revenu net)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, localcontext
from typing import Protocol

from app.economics.decimal_utils import engine_context, money
from app.economics.net_balance.parameters import NetBalanceParameters
from app.economics.net_balance.types import Status
from app.economics.types import Confidence


class IncomeLike(Protocol):
    """Forme minimale d'une ligne de revenu (compatible avec ``IncomeLine``)."""

    @property
    def status(self) -> Status: ...

    @property
    def net_income(self) -> Decimal: ...


@dataclass(frozen=True, slots=True)
class StatusCharges:
    status: Status
    net_income: Decimal
    employee_charges: Decimal  # arrondi au centime
    employer_charges: Decimal  # arrondi au centime
    employee_rate: Decimal
    employer_rate: Decimal
    provisional: bool
    state_expense: bool  # True pour fonctionnaire


def compute_charges(income: IncomeLike, params: NetBalanceParameters) -> StatusCharges:
    """Charges = ``money(revenu net × taux)``, taux appliqués renvoyés (même nuls)."""
    rates = params.rates[income.status]
    with localcontext(engine_context()):
        employee = money(income.net_income * rates.employee)
        employer = money(income.net_income * rates.employer)
    provisional = (
        rates.employee_row.confidence == Confidence.PROVISIONAL
        or rates.employer_row.confidence == Confidence.PROVISIONAL
    )
    return StatusCharges(
        status=income.status,
        net_income=income.net_income,
        employee_charges=employee,
        employer_charges=employer,
        employee_rate=rates.employee,
        employer_rate=rates.employer,
        provisional=provisional,
        state_expense=income.status == Status.FONCTIONNAIRE,
    )
