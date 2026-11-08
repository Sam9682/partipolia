"""Analyse des entrées brutes du bilan net (chaînes) en entrées typées.

Module pur : aucune I/O. Les montants arrivent en chaînes pour ne jamais passer
par un ``float``. Toutes les erreurs sont collectées en une seule passe et leurs
messages (en français) ne réémettent jamais la valeur saisie.

Les règles sur les catégories de consommation (catégories connues) sont
appliquées par ``compute_personal_savings`` : cette validation ne connaît pas le
snapshot. Elle vérifie la forme (montants, réductions dans ``[0, 100]``,
adultes dans ``[1, MAX_ADULTS]``) avec les mêmes bornes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.economics.decimal_utils import HUNDRED, ZERO, pct_to_fraction
from app.economics.net_balance.aids import MonetaryAid
from app.economics.net_balance.types import Status
from app.economics.personal import MAX_ADULTS

MAX_AMOUNT = Decimal(10_000_000)
MAX_CHILDREN = 20
MAX_LABEL_LENGTH = 80
MAX_NON_MONETARY_AIDS = 20

_AMOUNT_RE = re.compile(r"[0-9]+([.,][0-9]{1,2})?", re.ASCII)
_INTEGER_RE = re.compile(r"[0-9]+", re.ASCII)

_STATUS_LABELS: dict[Status, str] = {
    Status.SALARIE: "salarié",
    Status.SANS_EMPLOI: "sans emploi",
    Status.FONCTIONNAIRE: "fonctionnaire",
    Status.ENTREPRENEUR: "entrepreneur",
}

_AMOUNT_MESSAGE = "saisissez un montant positif ou nul (deux décimales au plus)."
_CAP_MESSAGE = "le montant dépasse le plafond autorisé de 10 000 000 €."


class IncomeLineRequest(Protocol):
    @property
    def status(self) -> Any: ...

    @property
    def net_income(self) -> Any: ...


class NonMonetaryAidRequest(Protocol):
    @property
    def label(self) -> Any: ...

    @property
    def value(self) -> Any: ...


class NetBalanceRequest(Protocol):
    """Requête brute (accès par attribut) ; satisfaite par le schéma Pydantic."""

    @property
    def incomes(self) -> Sequence[IncomeLineRequest] | None: ...

    @property
    def children(self) -> Any: ...

    @property
    def monetary_aids(self) -> Mapping[str, Any] | None: ...

    @property
    def non_monetary_aids(self) -> Sequence[NonMonetaryAidRequest] | None: ...

    @property
    def reductions(self) -> Mapping[str, Any] | None: ...

    @property
    def adults(self) -> Any: ...

    @property
    def reference_overrides(self) -> Mapping[str, Any] | None: ...

    @property
    def property_income(self) -> Any: ...

    @property
    def property_tax_rate(self) -> Any: ...


@dataclass(frozen=True, slots=True)
class FieldError:
    field: str
    message: str


@dataclass(frozen=True, slots=True)
class IncomeLine:
    status: Status
    net_income: Decimal


@dataclass(frozen=True, slots=True)
class NonMonetaryAid:
    label: str
    annual_value: Decimal


@dataclass(frozen=True, slots=True)
class NetBalanceInput:
    incomes: tuple[IncomeLine, ...]
    children: int
    monetary_aids: Mapping[MonetaryAid, Decimal]
    non_monetary_aids: tuple[NonMonetaryAid, ...]
    reductions: Mapping[str, Decimal]
    adults: int
    reference_overrides: Mapping[str, Decimal]
    property_income: Decimal = ZERO
    property_tax_rate: Decimal | None = None  # ratio dans [0, 1]


def _parse_amount(value: Any) -> tuple[Decimal | None, str | None]:
    """Retourne ``(montant, None)`` ou ``(None, message_sans_la_valeur)``."""
    if not isinstance(value, str) or _AMOUNT_RE.fullmatch(value) is None:
        return None, _AMOUNT_MESSAGE
    amount = Decimal(value.replace(",", "."))
    if amount < ZERO:
        return None, _AMOUNT_MESSAGE
    if amount > MAX_AMOUNT:
        return None, _CAP_MESSAGE
    return amount, None


def _parse_strict_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and _INTEGER_RE.fullmatch(value) is not None:
        return int(value)
    return None


def _status_key(raw: Any) -> Status | None:
    raw = getattr(raw, "value", raw)
    if not isinstance(raw, str):
        return None
    try:
        return Status(raw)
    except ValueError:
        return None


def _parse_incomes(raw: Sequence[Any] | None, errors: list[FieldError]) -> tuple[IncomeLine, ...]:
    lines: list[IncomeLine] = []
    items = list(raw or ())
    if len(items) >= 2:
        errors.append(
            FieldError(
                "revenus",
                "Un seul statut est autorisé : choisissez la situation qui décrit vos revenus.",
            )
        )
        return ()
    for index, item in enumerate(items):
        status = _status_key(getattr(item, "status", None))
        if status is None:
            errors.append(
                FieldError(f"revenus[{index}]", "Statut inconnu : choisissez un statut proposé.")
            )
            continue
        field = f"revenus.{status.value}"
        label = _STATUS_LABELS[status]
        amount, problem = _parse_amount(getattr(item, "net_income", None))
        if amount is None:
            errors.append(FieldError(field, f"Revenu net — {label} : {problem}"))
            continue
        lines.append(IncomeLine(status, amount))
    return tuple(lines)


def _parse_children(raw: Any, errors: list[FieldError]) -> int:
    if raw is None or raw == "":
        return 0
    children = _parse_strict_int(raw)
    if children is None or not 0 <= children <= MAX_CHILDREN:
        errors.append(
            FieldError(
                "enfants_a_charge",
                f"Enfants à charge : saisissez un nombre entier entre 0 et {MAX_CHILDREN}.",
            )
        )
        return 0
    return children


def _parse_monetary_aids(
    raw: Mapping[str, Any] | None, errors: list[FieldError]
) -> dict[MonetaryAid, Decimal]:
    aids: dict[MonetaryAid, Decimal] = {}
    for key, value in (raw or {}).items():
        key_str = getattr(key, "value", key)
        try:
            aid = MonetaryAid(key_str)
        except ValueError:
            errors.append(
                FieldError("aides", "Aide monétaire inconnue : choisissez une aide proposée.")
            )
            continue
        if value is None:
            continue  # aide non renseignée : ni estimation ni montant
        field = f"aides.{aid.value}"
        amount, problem = _parse_amount(value)
        if amount is None:
            errors.append(FieldError(field, f"Aide « {aid.value} » : {problem}"))
            continue
        aids[aid] = amount
    return aids


def _parse_non_monetary_aids(
    raw: Sequence[Any] | None, errors: list[FieldError]
) -> tuple[NonMonetaryAid, ...]:
    items = list(raw or ())
    if len(items) > MAX_NON_MONETARY_AIDS:
        errors.append(
            FieldError(
                "aides.non_monetaires",
                f"Aides non monétaires : {MAX_NON_MONETARY_AIDS} lignes au plus.",
            )
        )
        items = items[:MAX_NON_MONETARY_AIDS]
    parsed: list[NonMonetaryAid] = []
    for index, item in enumerate(items):
        field = f"aides.non_monetaires[{index}]"
        row = index + 1
        label_raw = getattr(item, "label", None)
        label = label_raw.strip() if isinstance(label_raw, str) else ""
        valid = True
        if not 1 <= len(label) <= MAX_LABEL_LENGTH:
            errors.append(
                FieldError(
                    f"{field}.libelle",
                    f"Aide non monétaire {row} : le libellé doit comporter "
                    f"de 1 à {MAX_LABEL_LENGTH} caractères.",
                )
            )
            valid = False
        amount, problem = _parse_amount(getattr(item, "value", None))
        if amount is None:
            errors.append(FieldError(f"{field}.valeur", f"Aide non monétaire {row} : {problem}"))
            valid = False
        if valid and amount is not None:
            parsed.append(NonMonetaryAid(label, amount))
    return tuple(parsed)


def _parse_reductions(
    raw: Mapping[str, Any] | None, errors: list[FieldError]
) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for code, value in (raw or {}).items():
        field = f"consommation.reductions.{code}"
        amount, problem = _parse_amount(value)
        if amount is None or amount > HUNDRED:
            errors.append(FieldError(field, "Réduction : saisissez un pourcentage entre 0 et 100."))
            continue
        result[str(code)] = amount
    return result


def _parse_overrides(raw: Mapping[str, Any] | None, errors: list[FieldError]) -> dict[str, Decimal]:
    result: dict[str, Decimal] = {}
    for code, value in (raw or {}).items():
        amount, problem = _parse_amount(value)
        if amount is None:
            errors.append(
                FieldError(
                    f"consommation.reference_overrides.{code}",
                    f"Dépense de référence : {problem}",
                )
            )
            continue
        result[str(code)] = amount
    return result


def _parse_adults(raw: Any, errors: list[FieldError]) -> int:
    if raw is None or raw == "":
        return 1
    adults = _parse_strict_int(raw)
    if adults is None or not 1 <= adults <= MAX_ADULTS:
        errors.append(
            FieldError(
                "consommation.adultes",
                f"Adultes : saisissez un nombre entier entre 1 et {MAX_ADULTS}.",
            )
        )
        return 1
    return adults


def _parse_property_income(raw: Any, errors: list[FieldError]) -> Decimal:
    if raw is None or raw == "":
        return ZERO
    amount, problem = _parse_amount(raw)
    if amount is None:
        errors.append(FieldError("immobilier.loyers_annuels", f"Loyers annuels : {problem}"))
        return ZERO
    return amount


def _parse_property_tax_rate(raw: Any, errors: list[FieldError]) -> Decimal | None:
    if raw is None or raw == "":
        return None
    percentage, _problem = _parse_amount(raw)
    if percentage is None or percentage > HUNDRED:
        errors.append(
            FieldError(
                "immobilier.taux_taxe_fonciere",
                "Taux de taxe foncière : saisissez un pourcentage entre 0 et 100.",
            )
        )
        return None
    return pct_to_fraction(percentage)


def parse_input(
    raw: NetBalanceRequest,
) -> tuple[NetBalanceInput | None, list[FieldError]]:
    """Valide la requête brute ; renvoie ``(entrée, [])`` ou ``(None, erreurs)``."""
    errors: list[FieldError] = []
    incomes = _parse_incomes(getattr(raw, "incomes", None), errors)
    children = _parse_children(getattr(raw, "children", None), errors)
    monetary = _parse_monetary_aids(getattr(raw, "monetary_aids", None), errors)
    non_monetary = _parse_non_monetary_aids(getattr(raw, "non_monetary_aids", None), errors)
    reductions = _parse_reductions(getattr(raw, "reductions", None), errors)
    adults = _parse_adults(getattr(raw, "adults", None), errors)
    overrides = _parse_overrides(getattr(raw, "reference_overrides", None), errors)
    property_income = _parse_property_income(getattr(raw, "property_income", None), errors)
    property_tax_rate = _parse_property_tax_rate(getattr(raw, "property_tax_rate", None), errors)
    if errors:
        return None, errors
    return (
        NetBalanceInput(
            incomes=incomes,
            children=children,
            monetary_aids=monetary,
            non_monetary_aids=non_monetary,
            reductions=reductions,
            adults=adults,
            reference_overrides=overrides,
            property_income=property_income,
            property_tax_rate=property_tax_rate,
        ),
        [],
    )
