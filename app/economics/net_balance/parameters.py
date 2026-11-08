"""Lecture et validation du CSV de paramètres du bilan net (pur, sans base de données).

Le fichier ``data/net_balance/net_balance_parameters.csv`` est la seule source de
vérité des taux moyens par statut et des barèmes d'aides liés aux enfants. Il est
chargé une fois par processus ; toute anomalie lève
:class:`NetBalanceParametersError` avec le fichier et le numéro de ligne.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from app.economics.decimal_utils import ONE, ZERO
from app.economics.net_balance.types import Status
from app.economics.parameters import parse_decimal
from app.economics.types import Confidence

PARAMETERS_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "net_balance" / ("net_balance_parameters.csv")
)
FILE_LABEL = "net_balance_parameters.csv"

COLUMNS = ("id", "sujet", "valeur", "unite", "source", "annee", "confiance")
UNITS = frozenset({"ratio_net", "eur_an", "eur_an_par_enfant", "entier"})

_EMPLOYEE_PREFIX = "charges_salariales."
_EMPLOYER_PREFIX = "charges_patronales."
_FAMILY_IDS = {
    "af_two": "allocations_familiales.deux_enfants",
    "af_extra": "allocations_familiales.enfant_supplementaire",
    "cf_min": "complement_familial.enfants_min",
    "cf_amount": "complement_familial.montant",
    "ars": "ars.par_enfant",
}


class NetBalanceParametersError(ValueError):
    """Erreur de chargement du CSV ; le message est en français."""


@dataclass(frozen=True, slots=True)
class NetBalanceParameterRow:
    id: str
    subject: str
    value: Decimal
    unit: str
    source: str
    year: int | None
    confidence: Confidence


@dataclass(frozen=True, slots=True)
class StatusRates:
    employee: Decimal  # taux de charges salariales appliqué au revenu net
    employer: Decimal  # taux de charges patronales appliqué au revenu net
    employee_row: NetBalanceParameterRow
    employer_row: NetBalanceParameterRow


@dataclass(frozen=True, slots=True)
class FamilyAidScale:
    allocations_two_children: Decimal
    allocations_extra_child: Decimal
    supplement_min_children: int
    supplement_amount: Decimal
    ars_per_child: Decimal
    allocations_two_children_row: NetBalanceParameterRow
    allocations_extra_child_row: NetBalanceParameterRow
    supplement_min_children_row: NetBalanceParameterRow
    supplement_amount_row: NetBalanceParameterRow
    ars_per_child_row: NetBalanceParameterRow


@dataclass(frozen=True, slots=True)
class NetBalanceParameters:
    rates: Mapping[Status, StatusRates]
    family_scale: FamilyAidScale
    rows: tuple[NetBalanceParameterRow, ...]

    def provisional_rows(self) -> tuple[NetBalanceParameterRow, ...]:
        """Lignes portant le marqueur « provisoire »."""
        return tuple(r for r in self.rows if r.confidence == Confidence.PROVISIONAL)


def _error(line: int, detail: str, label: str = FILE_LABEL) -> NetBalanceParametersError:
    return NetBalanceParametersError(f"{label}, ligne {line} : {detail}")


def parse_net_balance_csv(
    text: str, *, label: str = FILE_LABEL
) -> tuple[NetBalanceParameterRow, ...]:
    """Analyse le CSV ; lève ``NetBalanceParametersError`` citant la ligne fautive."""
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise NetBalanceParametersError(f"{label}, ligne 1 : fichier vide") from None
    except csv.Error as exc:
        raise _error(1, f"fichier illisible ({exc})", label) from exc
    if tuple(h.strip() for h in header) != COLUMNS:
        raise _error(1, f"colonnes attendues : {', '.join(COLUMNS)}", label)

    rows: list[NetBalanceParameterRow] = []
    seen: dict[str, int] = {}
    while True:
        try:
            cells = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            raise _error(reader.line_num, f"ligne illisible ({exc})", label) from exc
        line = reader.line_num
        if not any(c.strip() for c in cells):
            continue
        if len(cells) != len(COLUMNS):
            raise _error(line, f"{len(COLUMNS)} colonnes attendues, {len(cells)} trouvées", label)
        ident, subject, raw_value, unit, source, raw_year, raw_conf = (c.strip() for c in cells)
        if not ident:
            raise _error(line, "identifiant absent", label)
        if ident in seen:
            raise _error(line, f"identifiant en double '{ident}' (déjà ligne {seen[ident]})", label)
        seen[ident] = line
        if not subject:
            raise _error(line, f"sujet absent pour '{ident}'", label)
        if not raw_value:
            raise _error(line, f"valeur absente pour '{ident}'", label)
        try:
            value = parse_decimal(raw_value)
        except ValueError:
            raise _error(line, f"valeur non numérique pour '{ident}'", label) from None
        if unit not in UNITS:
            raise _error(line, f"unité inconnue pour '{ident}'", label)
        if unit == "ratio_net" and not (ZERO <= value <= ONE):
            raise _error(line, f"taux hors de [0, 1] pour '{ident}'", label)
        if unit != "ratio_net" and value < ZERO:
            raise _error(line, f"valeur négative pour '{ident}'", label)
        if unit == "entier" and value != value.to_integral_value():
            raise _error(line, f"entier attendu pour '{ident}'", label)
        year: int | None = None
        if raw_year:
            try:
                year = int(raw_year)
            except ValueError:
                raise _error(line, f"année invalide pour '{ident}'", label) from None
        try:
            confidence = Confidence(raw_conf)
        except ValueError:
            raise _error(line, f"niveau de confiance inconnu pour '{ident}'", label) from None
        rows.append(NetBalanceParameterRow(ident, subject, value, unit, source, year, confidence))
    return tuple(rows)


def build_parameters(
    rows: Sequence[NetBalanceParameterRow], *, label: str = FILE_LABEL
) -> NetBalanceParameters:
    """Construit les paramètres ; une ligne attendue absente est citée par son identifiant."""
    by_id = {r.id: r for r in rows}

    def need(ident: str) -> NetBalanceParameterRow:
        row = by_id.get(ident)
        if row is None:
            raise NetBalanceParametersError(f"{label}, ligne manquante : {ident}")
        return row

    rates: dict[Status, StatusRates] = {}
    for status in Status:
        emp = need(f"{_EMPLOYEE_PREFIX}{status.value}")
        pat = need(f"{_EMPLOYER_PREFIX}{status.value}")
        rates[status] = StatusRates(emp.value, pat.value, emp, pat)

    af_two = need(_FAMILY_IDS["af_two"])
    af_extra = need(_FAMILY_IDS["af_extra"])
    cf_min = need(_FAMILY_IDS["cf_min"])
    cf_amount = need(_FAMILY_IDS["cf_amount"])
    ars = need(_FAMILY_IDS["ars"])
    scale = FamilyAidScale(
        allocations_two_children=af_two.value,
        allocations_extra_child=af_extra.value,
        supplement_min_children=int(cf_min.value),
        supplement_amount=cf_amount.value,
        ars_per_child=ars.value,
        allocations_two_children_row=af_two,
        allocations_extra_child_row=af_extra,
        supplement_min_children_row=cf_min,
        supplement_amount_row=cf_amount,
        ars_per_child_row=ars,
    )
    return NetBalanceParameters(rates=MappingProxyType(rates), family_scale=scale, rows=tuple(rows))


@lru_cache(maxsize=1)
def load_net_balance_parameters() -> NetBalanceParameters:
    """Charge le CSV livré (mis en cache pour le processus)."""
    try:
        text = PARAMETERS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise NetBalanceParametersError(f"{FILE_LABEL} : fichier absent ou illisible") from exc
    return build_parameters(parse_net_balance_csv(text))
