"""Pydantic schemas of the net balance API (``POST /api/v1/net-balance/calculate``).

The request is deliberately lenient: every field is optional and permissively typed
(``Any``) so that format errors (numbers instead of strings, negative amounts, ...)
never raise a Pydantic error but go through ``parse_input``, which produces French
messages per field. The JSON uses French aliases; the model exposes the attribute
names that ``parse_input`` reads (``incomes``, ``children``, ...) as properties.

Response amounts are ``Decimal`` values serialized as strings in JSON, each paired
with its French form (``*_fr``).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class IncomeLineIn(BaseModel):
    """One income line: ``{"statut": "salarie", "revenu_net": "24000"}``."""

    model_config = ConfigDict(populate_by_name=True)

    statut: Any = None
    revenu_net: Any = None

    @property
    def status(self) -> Any:
        return self.statut

    @property
    def net_income(self) -> Any:
        return self.revenu_net


class NonMonetaryAidIn(BaseModel):
    """One non-monetary aid: ``{"libelle": "Cantine", "valeur": "300"}``."""

    model_config = ConfigDict(populate_by_name=True)

    libelle: Any = None
    valeur: Any = None

    @property
    def label(self) -> Any:
        return self.libelle

    @property
    def value(self) -> Any:
        return self.valeur


class AidsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    monetaires: dict[str, Any] | None = None
    non_monetaires: list[NonMonetaryAidIn] | None = None


class ConsumptionIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    reductions: dict[str, Any] | None = None
    adultes: Any = None
    reference_overrides: dict[str, Any] | None = None


class ImmobilierIn(BaseModel):
    """Real estate block: annual rents and property tax rate (percentage)."""

    model_config = ConfigDict(populate_by_name=True)

    loyers_annuels: Any = None
    taux_taxe_fonciere: Any = None


class NetBalanceRequest(BaseModel):
    """Input of the net balance endpoint (satisfies ``validation.NetBalanceRequest``)."""

    model_config = ConfigDict(populate_by_name=True)

    revenus: list[IncomeLineIn] | None = None
    enfants_a_charge: Any = None
    aides: AidsIn | None = None
    consommation: ConsumptionIn | None = None
    immobilier: ImmobilierIn | None = None

    @property
    def property_income(self) -> Any:
        return self.immobilier.loyers_annuels if self.immobilier is not None else None

    @property
    def property_tax_rate(self) -> Any:
        return self.immobilier.taux_taxe_fonciere if self.immobilier is not None else None

    @property
    def incomes(self) -> list[IncomeLineIn] | None:
        return self.revenus

    @property
    def children(self) -> Any:
        return self.enfants_a_charge

    @property
    def monetary_aids(self) -> dict[str, Any] | None:
        return self.aides.monetaires if self.aides is not None else None

    @property
    def non_monetary_aids(self) -> list[NonMonetaryAidIn] | None:
        return self.aides.non_monetaires if self.aides is not None else None

    @property
    def reductions(self) -> dict[str, Any] | None:
        return self.consommation.reductions if self.consommation is not None else None

    @property
    def adults(self) -> Any:
        return self.consommation.adultes if self.consommation is not None else None

    @property
    def reference_overrides(self) -> dict[str, Any] | None:
        return self.consommation.reference_overrides if self.consommation is not None else None


class UserIncomeOut(BaseModel):
    """Stored income of the logged-in user (``GET /incomes/me``); nulls when none."""

    statut: str | None = None
    revenu_net: Decimal | None = None


class StatusChargesOut(BaseModel):
    """Detail of one status: net income and estimated charges."""

    statut: str
    libelle: str
    revenu_net: Decimal
    revenu_net_fr: str
    charges_salariales: Decimal
    charges_salariales_fr: str
    charges_patronales: Decimal
    charges_patronales_fr: str
    taux_salarial: Decimal
    taux_patronal: Decimal
    provisoire: bool
    depense_etat: bool


class ConsumptionOut(BaseModel):
    tva: Decimal
    tva_fr: str
    accises_carburant: Decimal
    accises_carburant_fr: str
    tva_carburant: Decimal
    tva_carburant_fr: str
    total: Decimal
    total_fr: str


class AidEstimateOut(BaseModel):
    aide: str
    montant_annuel: Decimal
    montant_annuel_fr: str
    provisoire: bool


class ProvisionalParameterOut(BaseModel):
    id: str
    sujet: str
    valeur: Decimal
    unite: str
    source: str


class TrancheOut(BaseModel):
    indice: int
    libelle: str
    part_population: Decimal
    part_population_fr: str  # « 10,0 % »
    est_utilisateur: bool


class PositionnementOut(BaseModel):
    """Position of the household net contribution among ten population tranches."""

    tranches: list[TrancheOut]
    indice_utilisateur: int
    libelle_utilisateur: str
    texte_alternatif: str
    source_insee: str
    version_table: str
    annee_reference: int
    provisoire: bool
    avertissement: str


class NetBalanceResponse(BaseModel):
    """Output of the net balance endpoint: one verdict and its breakdown."""

    verdict: str
    verdict_libelle: str
    bilan: Decimal
    bilan_fr: str
    montant_mis_en_avant: Decimal  # |bilan|
    montant_mis_en_avant_fr: str

    statuts: list[StatusChargesOut]
    charges_salariales: Decimal
    charges_salariales_fr: str
    charges_patronales: Decimal
    charges_patronales_fr: str
    consommation: ConsumptionOut
    total_contributions: Decimal
    total_contributions_fr: str

    aides_monetaires: Decimal
    aides_monetaires_fr: str
    aides_non_monetaires: Decimal
    aides_non_monetaires_fr: str
    total_aides: Decimal
    total_aides_fr: str

    depense_etat: Decimal
    depense_etat_fr: str
    mention_fonctionnaire: str | None = None

    impot_revenu: Decimal
    impot_revenu_fr: str
    revenu_imposable: Decimal
    revenu_imposable_fr: str
    parts_fiscales: Decimal
    tranche_marginale: Decimal
    taux_moyen_effectif: Decimal
    impot_revenu_provisoire: bool
    revenus_immobiliers: Decimal
    revenus_immobiliers_fr: str
    taxe_fonciere: Decimal
    taxe_fonciere_fr: str
    taux_taxe_fonciere: Decimal  # pourcentage appliqué
    taux_taxe_fonciere_defaut: Decimal  # pourcentage par défaut du CSV

    estimations_aides: list[AidEstimateOut] = Field(default_factory=list)
    parametres_provisoires: list[str] = Field(default_factory=list)
    avertissement: str
    positionnement: PositionnementOut | None = None
