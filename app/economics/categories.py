"""Default consumption categories of the Partipolia simulator.

The reference annual expenditures are **initial configurable assumptions** taken
from the product specification. They are NOT official national averages. They are
seeded into the ``consumption_categories`` table, are editable by administrators,
and are meant to be calibrated later with INSEE household expenditure data.

This module is pure Python (no web or database imports) so it can be used by the
economic engine, the seed scripts and the presentation layer alike.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Final


@dataclass(frozen=True, slots=True)
class CategoryDefinition:
    """Static definition of a consumption category (French label for the UI)."""

    code: str
    name: str
    description: str
    reference_annual_expenditure: Decimal  # EUR per adult per year, TTC


DEFAULT_CATEGORIES: Final[tuple[CategoryDefinition, ...]] = (
    CategoryDefinition(
        code="restaurants",
        name="Restaurants, bars et cafés",
        description="Repas au restaurant, bars, cafés, restauration rapide.",
        reference_annual_expenditure=Decimal("1500"),
    ),
    CategoryDefinition(
        code="clothing",
        name="Vêtements et chaussures",
        description="Habillement et chaussures pour adultes.",
        reference_annual_expenditure=Decimal("470"),
    ),
    CategoryDefinition(
        code="leisure",
        name="Loisirs et divertissements",
        description="Cinéma, spectacles, sorties culturelles et sportives, loisirs.",
        reference_annual_expenditure=Decimal("600"),
    ),
    CategoryDefinition(
        code="hotels_holidays",
        name="Hôtels et vacances",
        description="Hébergement touristique, séjours et voyages d'agrément.",
        reference_annual_expenditure=Decimal("700"),
    ),
    CategoryDefinition(
        code="electronics",
        name="Électronique et équipement de la maison",
        description="Appareils électroniques, électroménager, équipement du foyer.",
        reference_annual_expenditure=Decimal("600"),
    ),
    CategoryDefinition(
        code="digital_subscriptions",
        name="Abonnements numériques",
        description="Streaming, plateformes, applications et services par abonnement.",
        reference_annual_expenditure=Decimal("300"),
    ),
)

CATEGORY_CODES: Final[tuple[str, ...]] = tuple(c.code for c in DEFAULT_CATEGORIES)

TOTAL_REFERENCE_EXPENDITURE: Final[Decimal] = sum(
    (c.reference_annual_expenditure for c in DEFAULT_CATEGORIES), Decimal("0")
)
