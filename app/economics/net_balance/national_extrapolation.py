"""Extrapolation nationale des charges, impôts et aides (calcul pur, sans I/O).

Les montants sont extrapolés aux Participants d'un scénario avec des taux moyens par
Statut lus dans ``net_balance_parameters.csv``. Ce module ne dépend ni de la base ni
du service : il ne fait que construire des paramètres typés (et, plus loin, calculer).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, localcontext
from types import MappingProxyType
from typing import Literal

from app.economics.decimal_utils import CENT, ONE, ZERO, engine_context
from app.economics.net_balance.parameters import (
    FILE_LABEL,
    NetBalanceParameterRow,
    NetBalanceParametersError,
)
from app.economics.net_balance.types import Status
from app.economics.types import Confidence

SUM_TOLERANCE = Decimal("0.0001")

LineKind = Literal["charge", "tax", "aid"]

#: Ordre fixe d'affichage (Exigence 4.5).
LINE_ORDER: tuple[str, ...] = (
    "charges_salariales",
    "charges_patronales",
    "impot_salaires",
    "impot_locatif",
    "apl_als",
    "allocations_familiales",
    "prime_activite",
    "rsa",
    "allocation_chomage",
    "complement_familial",
    "ars",
)
AID_CODES: tuple[str, ...] = LINE_ORDER[4:]
LINE_LABELS: Mapping[str, str] = MappingProxyType(
    {
        "charges_salariales": "Charges salariales",
        "charges_patronales": "Charges patronales",
        "impot_salaires": "Impôt sur les revenus de salaires",
        "impot_locatif": "Impôt sur les revenus locatifs",
        "apl_als": "APL / ALS",
        "allocations_familiales": "Allocations familiales",
        "prime_activite": "Prime d'activité",
        "rsa": "RSA",
        "allocation_chomage": "Allocation chômage",
        "complement_familial": "Complément familial",
        "ars": "Allocation de rentrée scolaire",
    }
)
#: Statuts exclus des charges : la rémunération d'un fonctionnaire est une dépense de l'État.
CHARGE_EXCLUDED: frozenset[Status] = frozenset({Status.FONCTIONNAIRE})

# Montants moyens des aides repris des lignes existantes (Exigence 3.3).
_EXISTING_AMOUNT_IDS: Mapping[str, str] = MappingProxyType(
    {
        "apl_als": "apl_als.montant_base",
        "allocations_familiales": "allocations_familiales.deux_enfants",
        "rsa": "rsa.forfait_personne_seule",
        "complement_familial": "complement_familial.montant",
        "ars": "ars.par_enfant",
    }
)
# Montants moyens ajoutés par la fonctionnalité.
_NEW_AMOUNT_IDS: Mapping[str, str] = MappingProxyType(
    {
        "allocation_chomage": "aides_montant.allocation_chomage",
        "prime_activite": "aides_montant.prime_activite",
    }
)
_RATIO_UNITS = frozenset({"ratio", "ratio_net"})
_AMOUNT_UNITS = frozenset({"eur_an", "eur_an_par_enfant"})


@dataclass(frozen=True, slots=True)
class StatusProfile:
    share: Decimal  # repartition_statuts.<statut>
    net_income: Decimal  # revenu_net_moyen.<statut>
    income_tax_rate: Decimal  # taux_moyen_ir.<statut>
    employee_rate: Decimal  # charges_salariales.<statut>
    employer_rate: Decimal  # charges_patronales.<statut>
    rows: tuple[NetBalanceParameterRow, ...]


@dataclass(frozen=True, slots=True)
class RentalProfile:
    share: Decimal  # part_locative.part_participants
    annual_rent: Decimal  # part_locative.loyer_annuel_moyen
    rows: tuple[NetBalanceParameterRow, ...]


@dataclass(frozen=True, slots=True)
class AidProfile:
    code: str
    beneficiary_share: Decimal
    annual_amount: Decimal
    rows: tuple[NetBalanceParameterRow, ...]


@dataclass(frozen=True, slots=True)
class ExtrapolationParameters:
    statuses: Mapping[Status, StatusProfile]
    rental: RentalProfile
    aids: tuple[AidProfile, ...]  # dans l'ordre de AID_CODES


@dataclass(frozen=True, slots=True)
class ExtrapolatedLineValue:
    code: str
    kind: LineKind
    amount: Decimal  # euros par an, arrondi au centime
    provisional_ids: tuple[str, ...]  # identifiants CSV provisoires utilisés


@dataclass(frozen=True, slots=True)
class ExtrapolationValue:
    lines: tuple[ExtrapolatedLineValue, ...]  # dans l'ordre de LINE_ORDER
    total_levies: Decimal
    total_aids: Decimal
    balance: Decimal


def build_extrapolation_parameters(
    rows: Sequence[NetBalanceParameterRow], *, label: str = FILE_LABEL
) -> ExtrapolationParameters:
    """Construit les paramètres d'extrapolation.

    Lève ``NetBalanceParametersError`` citant fichier + ligne ou identifiant
    (Exigences 6.3, 6.4).
    """
    by_id = {r.id: r for r in rows}

    def need(ident: str) -> NetBalanceParameterRow:
        row = by_id.get(ident)
        if row is None:
            raise NetBalanceParametersError(f"{label}, ligne manquante : {ident}")
        return row

    def need_ratio(ident: str) -> NetBalanceParameterRow:
        row = need(ident)
        if row.unit not in _RATIO_UNITS:
            raise _line_error(row, "unité ratio attendue", label)
        if not (ZERO <= row.value <= ONE):
            raise _line_error(row, "taux hors de [0, 1]", label)
        return row

    def need_amount(ident: str) -> NetBalanceParameterRow:
        row = need(ident)
        if row.unit not in _AMOUNT_UNITS:
            raise _line_error(row, "unité eur_an attendue", label)
        if row.value < ZERO:
            raise _line_error(row, "valeur négative", label)
        return row

    shares = {s: need_ratio(f"repartition_statuts.{s.value}") for s in Status}
    total = sum((r.value for r in shares.values()), ZERO)
    if abs(total - ONE) > SUM_TOLERANCE:
        ids = ", ".join(r.id for r in shares.values())
        raise NetBalanceParametersError(
            f"{label} : la somme de la répartition des statuts vaut {total} au lieu de 1 ({ids})"
        )

    statuses: dict[Status, StatusProfile] = {}
    for status in Status:
        share = shares[status]
        income = need_amount(f"revenu_net_moyen.{status.value}")
        ir = need_ratio(f"taux_moyen_ir.{status.value}")
        employee = need_ratio(f"charges_salariales.{status.value}")
        employer = need_ratio(f"charges_patronales.{status.value}")
        statuses[status] = StatusProfile(
            share=share.value,
            net_income=income.value,
            income_tax_rate=ir.value,
            employee_rate=employee.value,
            employer_rate=employer.value,
            rows=(share, income, ir, employee, employer),
        )

    rent_share = need_ratio("part_locative.part_participants")
    rent_amount = need_amount("part_locative.loyer_annuel_moyen")
    rental = RentalProfile(rent_share.value, rent_amount.value, (rent_share, rent_amount))

    amount_ids: Mapping[str, str] = {**_EXISTING_AMOUNT_IDS, **_NEW_AMOUNT_IDS}
    aids: list[AidProfile] = []
    for code in AID_CODES:
        beneficiaries = need_ratio(f"aides_beneficiaires.{code}")
        amount = need_amount(amount_ids[code])
        aids.append(AidProfile(code, beneficiaries.value, amount.value, (beneficiaries, amount)))

    return ExtrapolationParameters(
        statuses=MappingProxyType(statuses), rental=rental, aids=tuple(aids)
    )


def _cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def _provisional_ids(rows: Sequence[NetBalanceParameterRow]) -> tuple[str, ...]:
    """Identifiants CSV provisoires, dédoublonnés, dans l'ordre de première apparition."""
    seen: dict[str, None] = {}
    for row in rows:
        if row.confidence == Confidence.PROVISIONAL:
            seen.setdefault(row.id, None)
    return tuple(seen)


def _kind(code: str) -> LineKind:
    if code in AID_CODES:
        return "aid"
    return "charge" if code.startswith("charges_") else "tax"


def extrapolate(participants: Decimal, params: ExtrapolationParameters) -> ExtrapolationValue:
    """Calcule les 11 lignes, les totaux et le solde (pur, Decimal, un arrondi par ligne)."""
    with localcontext(engine_context()):
        statuses = params.statuses
        charged = [p for s, p in statuses.items() if s not in CHARGE_EXCLUDED]
        weighted_rate = sum((p.share * p.income_tax_rate for p in statuses.values()), ZERO)

        raw: dict[str, Decimal] = {
            "charges_salariales": sum(
                (participants * p.share * p.net_income * p.employee_rate for p in charged), ZERO
            ),
            "charges_patronales": sum(
                (participants * p.share * p.net_income * p.employer_rate for p in charged), ZERO
            ),
            "impot_salaires": sum(
                (
                    participants * p.share * p.net_income * p.income_tax_rate
                    for p in statuses.values()
                ),
                ZERO,
            ),
            "impot_locatif": participants
            * params.rental.share
            * params.rental.annual_rent
            * weighted_rate,
        }
        charged_rows = tuple(r for p in charged for r in p.rows)
        all_rows = tuple(r for p in statuses.values() for r in p.rows)
        used: dict[str, tuple[NetBalanceParameterRow, ...]] = {
            "charges_salariales": charged_rows,
            "charges_patronales": charged_rows,
            "impot_salaires": all_rows,
            "impot_locatif": params.rental.rows + all_rows,
        }
        for aid in params.aids:
            raw[aid.code] = participants * aid.beneficiary_share * aid.annual_amount
            used[aid.code] = aid.rows

        lines = tuple(
            ExtrapolatedLineValue(
                code=code,
                kind=_kind(code),
                amount=_cents(raw[code]),
                provisional_ids=_provisional_ids(used[code]),
            )
            for code in LINE_ORDER
        )
        levies = sum((x.amount for x in lines if x.kind != "aid"), ZERO)
        aids = sum((x.amount for x in lines if x.kind == "aid"), ZERO)
        return ExtrapolationValue(lines, levies, aids, levies - aids)


def _line_error(row: NetBalanceParameterRow, detail: str, label: str) -> NetBalanceParametersError:
    return NetBalanceParametersError(f"{label}, ligne {row.line} : {detail} pour '{row.id}'")


__all__: list[str] = [
    "AID_CODES",
    "CHARGE_EXCLUDED",
    "LINE_LABELS",
    "LINE_ORDER",
    "SUM_TOLERANCE",
    "AidProfile",
    "ExtrapolatedLineValue",
    "ExtrapolationParameters",
    "ExtrapolationValue",
    "LineKind",
    "RentalProfile",
    "StatusProfile",
    "build_extrapolation_parameters",
    "extrapolate",
]
