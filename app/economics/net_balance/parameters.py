"""Lecture et validation du CSV de paramètres du bilan net (pur, sans base de données).

Le fichier ``data/net_balance/net_balance_parameters.csv`` est la seule source de
vérité des taux moyens par statut et des barèmes d'aides liés aux enfants. Il est
chargé une fois par processus ; toute anomalie lève
:class:`NetBalanceParametersError` avec le fichier et le numéro de ligne.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING

from app.economics.decimal_utils import ONE, ZERO
from app.economics.net_balance.non_reducible import Line, NonReducibleParams, WeightedRate
from app.economics.net_balance.types import Status
from app.economics.parameters import parse_decimal
from app.economics.types import Confidence

if TYPE_CHECKING:
    from app.economics.net_balance.national_extrapolation import ExtrapolationParameters

PARAMETERS_FILE = (
    Path(__file__).resolve().parents[1] / "data" / "net_balance" / ("net_balance_parameters.csv")
)
FILE_LABEL = "net_balance_parameters.csv"

COLUMNS = ("id", "sujet", "valeur", "unite", "source", "annee", "confiance")
UNITS = frozenset(
    {"ratio_net", "eur_an", "eur_an_par_enfant", "entier", "ratio", "coefficient", "eur_an_par_uc"}
)
_RATIO_UNITS = frozenset({"ratio_net", "ratio"})
_BRACKET_RE = re.compile(r"^bareme_ir\.tranche_(\d+)\.(seuil|taux)$")
_PARTS_IDS = {
    "adult": "parts_fiscales.par_adulte",
    "child_1_2": "parts_fiscales.enfant_1_et_2",
    "child_3_plus": "parts_fiscales.enfant_3_et_plus",
}
_PROPERTY_TAX_RATE_ID = "taxe_fonciere.taux_defaut"
_POSITIONING_IDS = {
    "neutral_level": "positionnement.niveau_vie_neutre",
    "net_transfer_rate": "positionnement.taux_transfert_net",
}
_HOUSING_IDS = {
    "base": "apl_als.montant_base",
    "child": "apl_als.majoration_par_enfant",
    "taper": "apl_als.taux_degressivite",
}
_RSA_IDS = {
    "flat": "rsa.forfait_personne_seule",
    "adult": "rsa.coef_adulte_supplementaire",
    "child_1_2": "rsa.coef_enfant_1_et_2",
    "child_3_plus": "rsa.coef_enfant_3_et_plus",
}

_NR_MAX_AMOUNT = Decimal(1_000_000)
_NR_AMOUNT_IDS = {
    Line.NOURRITURE: "non_reductible.nourriture.montant_defaut",
    Line.ELECTRICITE: "non_reductible.electricite.montant_defaut",
    Line.ESSENCE: "non_reductible.essence.montant_defaut",
}
_FOOD_RATE_IDS = {
    "reduced": "tva.alimentation.taux_reduit",
    "intermediate": "tva.alimentation.taux_intermediaire",
    "normal": "tva.alimentation.taux_normal",
}
_FOOD_SHARE_IDS = {
    "reduced": "tva.alimentation.part_taux_reduit",
    "intermediate": "tva.alimentation.part_taux_intermediaire",
    "normal": "tva.alimentation.part_taux_normal",
}
_ELEC_IDS = {
    "abonnement": "tva.electricite.taux_abonnement",
    "consommation": "tva.electricite.taux_consommation",
    "part_abonnement": "tva.electricite.part_abonnement",
}
_FUEL_VAT_ID = "tva.essence.taux"

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
    line: int = 0  # numéro de ligne dans le CSV (0 si inconnu)


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
class TaxBracket:
    threshold: Decimal  # seuil inférieur (€ par part)
    rate: Decimal  # taux de la tranche, dans [0, 1]
    threshold_row: NetBalanceParameterRow
    rate_row: NetBalanceParameterRow


@dataclass(frozen=True, slots=True)
class IncomeTaxScale:
    brackets: tuple[TaxBracket, ...]  # seuils strictement croissants, le premier vaut 0
    adult_parts: Decimal
    child_parts_first_two: Decimal
    child_parts_third_plus: Decimal
    parts_rows: tuple[NetBalanceParameterRow, ...]


@dataclass(frozen=True, slots=True)
class PropertyTaxParams:
    default_rate: Decimal  # ratio, ex. 0.12
    default_rate_row: NetBalanceParameterRow


@dataclass(frozen=True, slots=True)
class IncomeAidScale:
    housing_base: Decimal  # €/an
    housing_per_child: Decimal  # €/an par enfant
    housing_taper: Decimal  # ratio de dégressivité sur les ressources
    rsa_flat: Decimal  # forfait annuel pour une personne seule
    rsa_extra_adult: Decimal  # coefficient par adulte supplémentaire
    rsa_child_first_two: Decimal  # coefficient par enfant (1er et 2e)
    rsa_child_third_plus: Decimal  # coefficient par enfant à partir du 3e
    housing_rows: tuple[NetBalanceParameterRow, ...]
    rsa_rows: tuple[NetBalanceParameterRow, ...]


@dataclass(frozen=True, slots=True)
class PositioningParams:
    neutral_level: Decimal  # niveau de vie neutre, €/an par unité de consommation
    net_transfer_rate: Decimal  # ratio du transfert net, dans [0, 1]
    neutral_level_row: NetBalanceParameterRow
    net_transfer_rate_row: NetBalanceParameterRow


@dataclass(frozen=True, slots=True)
class NetBalanceParameters:
    rates: Mapping[Status, StatusRates]
    family_scale: FamilyAidScale
    income_tax: IncomeTaxScale
    property_tax: PropertyTaxParams
    income_aids: IncomeAidScale
    positioning: PositioningParams
    non_reducible: NonReducibleParams
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
        if unit in _RATIO_UNITS and not (ZERO <= value <= ONE):
            raise _error(line, f"taux hors de [0, 1] pour '{ident}'", label)
        if unit not in _RATIO_UNITS and value < ZERO:
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
        rows.append(
            NetBalanceParameterRow(ident, subject, value, unit, source, year, confidence, line)
        )
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
    income_tax = _build_income_tax(by_id, need, label)
    property_tax_row = need(_PROPERTY_TAX_RATE_ID)
    property_tax = PropertyTaxParams(
        default_rate=property_tax_row.value, default_rate_row=property_tax_row
    )
    housing = {k: need(v) for k, v in _HOUSING_IDS.items()}
    rsa = {k: need(v) for k, v in _RSA_IDS.items()}
    income_aids = IncomeAidScale(
        housing_base=housing["base"].value,
        housing_per_child=housing["child"].value,
        housing_taper=housing["taper"].value,
        rsa_flat=rsa["flat"].value,
        rsa_extra_adult=rsa["adult"].value,
        rsa_child_first_two=rsa["child_1_2"].value,
        rsa_child_third_plus=rsa["child_3_plus"].value,
        housing_rows=tuple(housing.values()),
        rsa_rows=tuple(rsa.values()),
    )
    neutral_row = need(_POSITIONING_IDS["neutral_level"])
    transfer_row = need(_POSITIONING_IDS["net_transfer_rate"])
    if neutral_row.unit != "eur_an_par_uc":
        raise _error(neutral_row.line, f"unité eur_an_par_uc attendue pour {neutral_row.id}", label)
    if transfer_row.unit != "ratio":
        raise _error(transfer_row.line, f"unité ratio attendue pour {transfer_row.id}", label)
    if neutral_row.value <= ZERO:
        raise _error(
            neutral_row.line, f"valeur strictement positive attendue ({neutral_row.id})", label
        )
    positioning = PositioningParams(
        neutral_level=neutral_row.value,
        net_transfer_rate=transfer_row.value,
        neutral_level_row=neutral_row,
        net_transfer_rate_row=transfer_row,
    )
    non_reducible = _build_non_reducible(need, label)
    return NetBalanceParameters(
        rates=MappingProxyType(rates),
        family_scale=scale,
        income_tax=income_tax,
        property_tax=property_tax,
        income_aids=income_aids,
        positioning=positioning,
        non_reducible=non_reducible,
        rows=tuple(rows),
    )


def _build_non_reducible(
    need: Callable[[str], NetBalanceParameterRow], label: str
) -> NonReducibleParams:
    amount_rows = {line: need(ident) for line, ident in _NR_AMOUNT_IDS.items()}
    for row in amount_rows.values():
        if not (ZERO <= row.value <= _NR_MAX_AMOUNT):
            raise _error(row.line, f"montant hors de [0, 1000000] pour {row.id}", label)

    food = {k: need(v) for k, v in _FOOD_RATE_IDS.items()}
    food_shares = {k: need(v) for k, v in _FOOD_SHARE_IDS.items()}
    if sum((r.value for r in food_shares.values()), ZERO) != ONE:
        raise _error(
            next(iter(food_shares.values())).line,
            "les parts de pondération alimentaires doivent sommer à 1 "
            f"({', '.join(r.id for r in food_shares.values())})",
            label,
        )
    food_rate = sum((food[k].value * food_shares[k].value for k in food), ZERO)

    sub_rate = need(_ELEC_IDS["abonnement"])
    cons_rate = need(_ELEC_IDS["consommation"])
    sub_share = need(_ELEC_IDS["part_abonnement"])
    if not (ZERO <= sub_share.value <= ONE):
        raise _error(sub_share.line, f"part hors de [0, 1] pour {sub_share.id}", label)
    elec_rate = sub_share.value * sub_rate.value + (ONE - sub_share.value) * cons_rate.value
    # La répartition n'influence le résultat (et n'est « utilisée ») que si les taux diffèrent.
    elec_rows = (
        (sub_rate, cons_rate, sub_share) if sub_rate.value != cons_rate.value else (sub_rate,)
    )

    fuel_vat = need(_FUEL_VAT_ID)
    return NonReducibleParams(
        default_amounts=MappingProxyType({ln: r.value for ln, r in amount_rows.items()}),
        default_rows=MappingProxyType(amount_rows),
        food_rate=WeightedRate(food_rate, (*food.values(), *food_shares.values())),
        electricity_rate=WeightedRate(elec_rate, elec_rows),
        fuel_vat_rate=fuel_vat.value,
        fuel_vat_row=fuel_vat,
        sources=MappingProxyType(
            {
                Line.NOURRITURE: (amount_rows[Line.NOURRITURE], *food.values()),
                Line.ELECTRICITE: (amount_rows[Line.ELECTRICITE], sub_rate, cons_rate),
                Line.ESSENCE: (amount_rows[Line.ESSENCE], fuel_vat),
            }
        ),
    )


def _build_income_tax(
    by_id: Mapping[str, NetBalanceParameterRow],
    need: Callable[[str], NetBalanceParameterRow],
    label: str,
) -> IncomeTaxScale:
    numbers = sorted({int(m.group(1)) for ident in by_id if (m := _BRACKET_RE.match(ident))})
    if not numbers:
        raise NetBalanceParametersError(f"{label}, ligne manquante : bareme_ir.tranche_1.seuil")
    brackets: list[TaxBracket] = []
    for n in range(1, numbers[-1] + 1):
        threshold_row = need(f"bareme_ir.tranche_{n}.seuil")
        rate_row = need(f"bareme_ir.tranche_{n}.taux")
        if threshold_row.unit != "eur_an":
            raise _error(
                threshold_row.line, f"unité eur_an attendue pour {threshold_row.id}", label
            )
        if rate_row.unit != "ratio":
            raise _error(rate_row.line, f"unité ratio attendue pour {rate_row.id}", label)
        if not brackets and threshold_row.value != ZERO:
            raise _error(
                threshold_row.line, f"le premier seuil doit valoir 0 ({threshold_row.id})", label
            )
        if brackets and threshold_row.value <= brackets[-1].threshold:
            raise _error(
                threshold_row.line,
                f"seuils strictement croissants attendus ({threshold_row.id})",
                label,
            )
        brackets.append(TaxBracket(threshold_row.value, rate_row.value, threshold_row, rate_row))
    parts = {k: need(v) for k, v in _PARTS_IDS.items()}
    for row in parts.values():
        if row.unit != "coefficient":
            raise _error(row.line, f"unité coefficient attendue pour {row.id}", label)
    return IncomeTaxScale(
        brackets=tuple(brackets),
        adult_parts=parts["adult"].value,
        child_parts_first_two=parts["child_1_2"].value,
        child_parts_third_plus=parts["child_3_plus"].value,
        parts_rows=tuple(parts.values()),
    )


@lru_cache(maxsize=1)
def load_net_balance_parameters() -> NetBalanceParameters:
    """Charge le CSV livré (mis en cache pour le processus)."""
    try:
        text = PARAMETERS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise NetBalanceParametersError(f"{FILE_LABEL} : fichier absent ou illisible") from exc
    return build_parameters(parse_net_balance_csv(text))


@lru_cache(maxsize=1)
def load_extrapolation_parameters() -> ExtrapolationParameters:
    """Charge le CSV livré pour l'extrapolation nationale (cache de processus)."""
    # Import local : national_extrapolation importe ce module (import circulaire).
    from app.economics.net_balance.national_extrapolation import build_extrapolation_parameters

    try:
        text = PARAMETERS_FILE.read_text(encoding="utf-8")
    except OSError as exc:
        raise NetBalanceParametersError(f"{FILE_LABEL} : fichier absent ou illisible") from exc
    return build_extrapolation_parameters(parse_net_balance_csv(text))
