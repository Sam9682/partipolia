"""Positionnement de la contribution nette d'un foyer parmi dix tranches de population.

Mapping linéaire : ``Bk = taux × UC × (Dk − niveau_vie_neutre)`` pour k = 1..9.
Tranches numérotées de 1 (plus contributrice) à 10 (plus bénéficiaire), intervalles
ouverts à gauche et fermés à droite ; une valeur égale à une borne va dans la tranche
inférieure (plus bénéficiaire). Calcul pur, en ``Decimal``, sans accès réseau.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, localcontext

from app.economics.decimal_utils import ONE, engine_context
from app.economics.net_balance.parameters import PositioningParams
from app.economics.population_deciles import DECILE_COUNT, DecileTable
from app.economics.types import Confidence

TRANCHE_COUNT = DECILE_COUNT + 1
_ADULT_EXTRA = Decimal("0.5")
_CHILD = Decimal("0.3")
_SHARE_STEP = Decimal("0.1")

LABEL_TOP_CONTRIBUTORS = "Les 10 % qui contribuent le plus"
LABEL_TOP_BENEFICIARIES = "Les 10 % qui bénéficient le plus"


@dataclass(frozen=True, slots=True)
class Tranche:
    index: int  # 1 = plus contributrice ... 10 = plus bénéficiaire
    label: str
    share_percent: Decimal  # une décimale
    lower_bound: Decimal | None  # exclue ; None pour la tranche 10
    upper_bound: Decimal | None  # incluse ; None pour la tranche 1


@dataclass(frozen=True, slots=True)
class Positioning:
    tranches: tuple[Tranche, ...]  # ordonnées de 1 à 10
    user_index: int
    table_version: str
    source_url: str
    reference_year: int
    provisional: bool


def household_units(adults: int, children: int) -> Decimal:
    """Unités de consommation (échelle OCDE modifiée) : 1 + 0,5 × (adultes − 1) + 0,3 × enfants."""
    if adults < 1:
        raise ValueError("au moins un adulte est requis")
    if children < 0:
        raise ValueError("le nombre d'enfants ne peut pas être négatif")
    with localcontext(engine_context()):
        return ONE + _ADULT_EXTRA * (adults - 1) + _CHILD * children


def split_shares(count: int) -> tuple[Decimal, ...]:
    """Répartit 100,0 sur ``count`` parts à une décimale (plus fort reste), somme exacte."""
    if count < 1:
        raise ValueError("count doit être au moins 1")
    tenths_total = 1000  # 100,0 en dixièmes
    base, remainder = divmod(tenths_total, count)
    # Parts égales : le reste (en dixièmes) est distribué une unité aux premières parts.
    tenths = [base + (1 if i < remainder else 0) for i in range(count)]
    return tuple(Decimal(t) * _SHARE_STEP for t in tenths)


def _label(index: int) -> str:
    if index == 1:
        return LABEL_TOP_CONTRIBUTORS
    if index == TRANCHE_COUNT:
        return LABEL_TOP_BENEFICIARIES
    return f"Déciles de contribution nette n° {index}"


def build_tranches(
    table: DecileTable, units: Decimal, rate: Decimal, neutral: Decimal
) -> tuple[Tranche, ...]:
    """Construit les dix tranches ordonnées de 1 à 10 à partir des bornes B1..B9."""
    with localcontext(engine_context()):
        bounds = [rate * units * (row.niveau_vie - neutral) for row in table.rows]  # B1..B9
    shares = split_shares(TRANCHE_COUNT)
    tranches: list[Tranche] = []
    for index in range(1, TRANCHE_COUNT + 1):
        # Tranche k : B(10−k) < c ≤ B(11−k) ; Bj = bounds[j − 1].
        lower = bounds[DECILE_COUNT - index] if index < TRANCHE_COUNT else None
        upper = bounds[DECILE_COUNT - index + 1] if index > 1 else None
        tranches.append(Tranche(index, _label(index), shares[index - 1], lower, upper))
    return tuple(tranches)


def locate_tranche(balance: Decimal, tranches: Sequence[Tranche]) -> int:
    """Indice (1..10) de la tranche contenant ``balance`` (gauche ouvert, droite fermé)."""
    for tranche in tranches:
        above_lower = tranche.lower_bound is None or balance > tranche.lower_bound
        within_upper = tranche.upper_bound is None or balance <= tranche.upper_bound
        if above_lower and within_upper:
            return tranche.index
    raise ValueError("aucune tranche ne contient la contribution")


def compute_positioning(
    balance: Decimal,
    adults: int,
    children: int,
    table: DecileTable,
    params: PositioningParams,
) -> Positioning:
    """Situe la contribution nette annuelle du foyer parmi les dix tranches."""
    units = household_units(adults, children)
    tranches = build_tranches(table, units, params.net_transfer_rate, params.neutral_level)
    provisional = Confidence.PROVISIONAL in (
        params.neutral_level_row.confidence,
        params.net_transfer_rate_row.confidence,
    )
    return Positioning(
        tranches=tranches,
        user_index=locate_tranche(balance, tranches),
        table_version=table.version,
        source_url=table.source_url,
        reference_year=table.reference_year,
        provisional=provisional,
    )


__all__ = [
    "Positioning",
    "Tranche",
    "build_tranches",
    "compute_positioning",
    "household_units",
    "locate_tranche",
    "split_shares",
]
