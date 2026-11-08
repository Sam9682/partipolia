"""Types partagés du module de bilan net (purs, sans accès base ni I/O)."""

from __future__ import annotations

from enum import StrEnum


class Status(StrEnum):
    """Situation professionnelle d'une ligne de revenu."""

    SALARIE = "salarie"
    SANS_EMPLOI = "sans_emploi"
    FONCTIONNAIRE = "fonctionnaire"
    ENTREPRENEUR = "entrepreneur"
