"""Estimation des aides liées aux enfants et totaux d'aides (pur, sans I/O).

Les estimations sont seulement *proposées* au client. Le serveur n'ajoute jamais
une estimation de lui-même : seuls les montants transmis entrent dans les totaux.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.economics.decimal_utils import ZERO, engine_context, money
from app.economics.net_balance.parameters import (
    FamilyAidScale,
    IncomeAidScale,
    NetBalanceParameterRow,
)
from app.economics.types import Confidence


class MonetaryAid(StrEnum):
    """Aides monétaires proposées dans le bloc Aides."""

    APL_ALS = "apl_als"
    ALLOCATIONS_FAMILIALES = "allocations_familiales"
    PRIME_ACTIVITE = "prime_activite"
    RSA = "rsa"
    ALLOCATION_CHOMAGE = "allocation_chomage"
    COMPLEMENT_FAMILIAL = "complement_familial"
    ARS = "ars"


CHILD_DEPENDENT: frozenset[MonetaryAid] = frozenset(
    {
        MonetaryAid.ALLOCATIONS_FAMILIALES,
        MonetaryAid.COMPLEMENT_FAMILIAL,
        MonetaryAid.ARS,
    }
)


@dataclass(frozen=True, slots=True)
class AidEstimate:
    aid: MonetaryAid
    annual_amount: Decimal
    provisional: bool


@dataclass(frozen=True, slots=True)
class AidTotals:
    monetary: Decimal
    non_monetary: Decimal
    total: Decimal


def _is_provisional(*rows: NetBalanceParameterRow) -> bool:
    return any(r.confidence == Confidence.PROVISIONAL for r in rows)


def estimate_child_aids(children: int, scale: FamilyAidScale) -> dict[MonetaryAid, AidEstimate]:
    """Estime les aides dépendant du nombre d'enfants avec les barèmes du CSV."""
    if isinstance(children, bool) or not isinstance(children, int) or children < 0:
        raise ValueError("Le nombre d'enfants doit être un entier positif ou nul.")

    ctx = engine_context()
    if children < 2:
        allocations = ZERO
    elif children == 2:
        allocations = scale.allocations_two_children
    else:
        extra = ctx.multiply(Decimal(children - 2), scale.allocations_extra_child)
        allocations = ctx.add(scale.allocations_two_children, extra)

    supplement = scale.supplement_amount if children >= scale.supplement_min_children else ZERO
    ars = ctx.multiply(Decimal(children), scale.ars_per_child)

    allocations_rows = [scale.allocations_two_children_row]
    if children > 2:
        allocations_rows.append(scale.allocations_extra_child_row)

    return {
        MonetaryAid.ALLOCATIONS_FAMILIALES: AidEstimate(
            MonetaryAid.ALLOCATIONS_FAMILIALES,
            money(allocations),
            _is_provisional(*allocations_rows),
        ),
        MonetaryAid.COMPLEMENT_FAMILIAL: AidEstimate(
            MonetaryAid.COMPLEMENT_FAMILIAL,
            money(supplement),
            _is_provisional(scale.supplement_min_children_row, scale.supplement_amount_row),
        ),
        MonetaryAid.ARS: AidEstimate(
            MonetaryAid.ARS,
            money(ars),
            _is_provisional(scale.ars_per_child_row),
        ),
    }


def estimate_income_aids(
    net_income: Decimal,
    property_income: Decimal,
    adults: int,
    children: int,
    scale: IncomeAidScale,
) -> dict[MonetaryAid, AidEstimate]:
    """Estime APL/ALS et RSA à partir des ressources et de la composition du foyer."""
    if isinstance(children, bool) or not isinstance(children, int) or children < 0:
        raise ValueError("Le nombre d'enfants doit être un entier positif ou nul.")
    if isinstance(adults, bool) or not isinstance(adults, int) or adults < 1:
        raise ValueError("Le nombre d'adultes doit être un entier supérieur ou égal à 1.")

    ctx = engine_context()
    resources = ctx.add(net_income, property_income)

    # APL/ALS : base + majoration par enfant - dégressivité x ressources, plancher à 0.
    gross_housing = ctx.add(
        scale.housing_base, ctx.multiply(Decimal(children), scale.housing_per_child)
    )
    housing = max(ZERO, ctx.subtract(gross_housing, ctx.multiply(scale.housing_taper, resources)))

    # RSA : plafond du foyer - ressources, plancher à 0.
    coefficient = ctx.add(Decimal(1), ctx.multiply(scale.rsa_extra_adult, Decimal(adults - 1)))
    coefficient = ctx.add(
        coefficient, ctx.multiply(scale.rsa_child_first_two, Decimal(min(children, 2)))
    )
    coefficient = ctx.add(
        coefficient, ctx.multiply(scale.rsa_child_third_plus, Decimal(max(children - 2, 0)))
    )
    ceiling = ctx.multiply(scale.rsa_flat, coefficient)
    rsa = max(ZERO, ctx.subtract(ceiling, resources))

    return {
        MonetaryAid.APL_ALS: AidEstimate(
            MonetaryAid.APL_ALS, money(housing), _is_provisional(*scale.housing_rows)
        ),
        MonetaryAid.RSA: AidEstimate(MonetaryAid.RSA, money(rsa), _is_provisional(*scale.rsa_rows)),
    }


def compute_aid_totals(
    monetary: Mapping[MonetaryAid, Decimal], non_monetary: Iterable[Decimal]
) -> AidTotals:
    """Sous-totaux et total sur des montants arrondis au centime avant addition."""
    monetary_total = sum((money(v) for v in monetary.values()), ZERO)
    non_monetary_total = sum((money(v) for v in non_monetary), ZERO)
    return AidTotals(
        monetary=monetary_total,
        non_monetary=non_monetary_total,
        total=monetary_total + non_monetary_total,
    )
