"""Net balance service: glue between the pure net balance module and the API.

Stateless and read-only: nothing is written to the database or to a cache, and no
logger is used on the calculation path so that entered values can never leak into
logs. Error messages never echo the entered values.
"""

from __future__ import annotations

from decimal import Decimal

from app.economics.net_balance import compute_net_balance, load_net_balance_parameters
from app.economics.net_balance.balance import NetBalanceResult, Verdict
from app.economics.net_balance.parameters import NetBalanceParameters
from app.economics.net_balance.types import Status
from app.economics.net_balance.validation import FieldError, parse_input
from app.economics.population_deciles import load_decile_table
from app.economics.positioning import Positioning, compute_positioning
from app.economics.types import Confidence
from app.schemas.net_balance import (
    AidEstimateOut,
    ConsumptionOut,
    NetBalanceRequest,
    NetBalanceResponse,
    PositionnementOut,
    StatusChargesOut,
    TrancheOut,
)
from app.services.simulation_service import SnapshotProvider
from app.web.formatting import format_eur_fr, format_number_fr

__all__ = ["NetBalanceService", "NetBalanceValidationError"]

WARNING_TEXT = (
    "Ce bilan est une estimation fondée sur des taux moyens simplifiés, "
    "et non un calcul fiscal individuel."
)
CIVIL_SERVANT_NOTE = (
    "La rémunération d'un fonctionnaire est une dépense de l'État : "
    "sa contribution provient de sa consommation."
)

POSITIONING_WARNING_TEXT = (
    "Estimation fondée sur des moyennes de revenus INSEE "
    "(niveau de vie par unité de consommation) et non sur vos revenus réels."
)

_VERDICT_LABELS: dict[Verdict, str] = {
    Verdict.CONTRIBUTEUR_NET: "Contributeur net",
    Verdict.BENEFICIAIRE_NET: "Bénéficiaire net",
    Verdict.SOLDE_NUL: "Solde nul",
}

_STATUS_LABELS: dict[Status, str] = {
    Status.SALARIE: "Salarié",
    Status.SANS_EMPLOI: "Sans emploi",
    Status.FONCTIONNAIRE: "Fonctionnaire",
    Status.ENTREPRENEUR: "Entrepreneur",
}

_UNKNOWN_CATEGORY_MESSAGE = (
    "Catégorie de consommation inconnue : utilisez uniquement les catégories proposées."
)


class NetBalanceValidationError(ValueError):
    """Invalid request; the router converts it to a 400 response."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("Requête invalide")
        self.errors = errors


def _fr(value: Decimal) -> str:
    return format_eur_fr(value, places=2)


def _share_fr(value: Decimal) -> str:
    return f"{format_number_fr(value, 1)} %"


def _build_positioning(positioning: Positioning) -> PositionnementOut:
    user = positioning.tranches[positioning.user_index - 1]
    return PositionnementOut(
        tranches=[
            TrancheOut(
                indice=t.index,
                libelle=t.label,
                part_population=t.share_percent,
                part_population_fr=_share_fr(t.share_percent),
                est_utilisateur=t.index == positioning.user_index,
            )
            for t in positioning.tranches
        ],
        indice_utilisateur=user.index,
        libelle_utilisateur=user.label,
        texte_alternatif=(
            f"Vous vous situez dans la tranche « {user.label} », "
            f"qui représente {_share_fr(user.share_percent)} de la population."
        ),
        source_insee=positioning.source_url,
        version_table=positioning.table_version,
        annee_reference=positioning.reference_year,
        provisoire=positioning.provisional,
        avertissement=POSITIONING_WARNING_TEXT,
    )


class NetBalanceService:
    def __init__(self, provider: SnapshotProvider) -> None:
        self._provider = provider

    async def calculate(self, request: NetBalanceRequest) -> NetBalanceResponse:
        data, errors = parse_input(request)
        if data is None:
            raise NetBalanceValidationError(errors)
        params = load_net_balance_parameters()
        snapshot = await self._provider.get_snapshot()
        try:
            result = compute_net_balance(data, params, snapshot)
        except KeyError:
            # Unknown consumption category code: never echo the entered value.
            raise NetBalanceValidationError(
                [FieldError("consommation", _UNKNOWN_CATEGORY_MESSAGE)]
            ) from None
        return self._build_response(result, params, data.adults, data.children)

    @staticmethod
    def _build_response(
        result: NetBalanceResult,
        params: NetBalanceParameters,
        adults: int,
        children: int,
    ) -> NetBalanceResponse:
        consumption = result.consumption
        income_tax = result.income_tax
        property_tax = result.property_tax
        hundred = Decimal(100)
        has_civil_servant = any(c.state_expense for c in result.charges)
        positioning = compute_positioning(
            result.balance, adults, children, load_decile_table(), params.positioning
        )
        return NetBalanceResponse(
            verdict=result.verdict.value,
            verdict_libelle=_VERDICT_LABELS[result.verdict],
            bilan=result.balance,
            bilan_fr=_fr(result.balance),
            montant_mis_en_avant=abs(result.balance),
            montant_mis_en_avant_fr=_fr(abs(result.balance)),
            statuts=[
                StatusChargesOut(
                    statut=c.status.value,
                    libelle=_STATUS_LABELS[c.status],
                    revenu_net=c.net_income,
                    revenu_net_fr=_fr(c.net_income),
                    charges_salariales=c.employee_charges,
                    charges_salariales_fr=_fr(c.employee_charges),
                    charges_patronales=c.employer_charges,
                    charges_patronales_fr=_fr(c.employer_charges),
                    taux_salarial=c.employee_rate,
                    taux_patronal=c.employer_rate,
                    provisoire=c.provisional,
                    depense_etat=c.state_expense,
                )
                for c in result.charges
            ],
            charges_salariales=result.employee_charges_total,
            charges_salariales_fr=_fr(result.employee_charges_total),
            charges_patronales=result.employer_charges_total,
            charges_patronales_fr=_fr(result.employer_charges_total),
            consommation=ConsumptionOut(
                tva=consumption.vat,
                tva_fr=_fr(consumption.vat),
                accises_carburant=consumption.fuel_excise,
                accises_carburant_fr=_fr(consumption.fuel_excise),
                tva_carburant=consumption.fuel_vat,
                tva_carburant_fr=_fr(consumption.fuel_vat),
                total=consumption.total,
                total_fr=_fr(consumption.total),
            ),
            total_contributions=result.contributions_total,
            total_contributions_fr=_fr(result.contributions_total),
            aides_monetaires=result.monetary_aids,
            aides_monetaires_fr=_fr(result.monetary_aids),
            aides_non_monetaires=result.non_monetary_aids,
            aides_non_monetaires_fr=_fr(result.non_monetary_aids),
            total_aides=result.aids_total,
            total_aides_fr=_fr(result.aids_total),
            depense_etat=result.state_expense_total,
            depense_etat_fr=_fr(result.state_expense_total),
            mention_fonctionnaire=CIVIL_SERVANT_NOTE if has_civil_servant else None,
            impot_revenu=income_tax.tax,
            impot_revenu_fr=_fr(income_tax.tax),
            revenu_imposable=income_tax.taxable_income,
            revenu_imposable_fr=_fr(income_tax.taxable_income),
            parts_fiscales=income_tax.parts,
            tranche_marginale=income_tax.marginal_rate,
            taux_moyen_effectif=income_tax.average_rate,
            impot_revenu_provisoire=any(
                r.confidence == Confidence.PROVISIONAL for r in income_tax.used_rows
            ),
            revenus_immobiliers=property_tax.property_income,
            revenus_immobiliers_fr=_fr(property_tax.property_income),
            taxe_fonciere=property_tax.tax,
            taxe_fonciere_fr=_fr(property_tax.tax),
            taux_taxe_fonciere=property_tax.rate * hundred,
            taux_taxe_fonciere_defaut=params.property_tax.default_rate * hundred,
            estimations_aides=[
                AidEstimateOut(
                    aide=estimate.aid.value,
                    montant_annuel=estimate.annual_amount,
                    montant_annuel_fr=_fr(estimate.annual_amount),
                    provisoire=estimate.provisional,
                )
                for estimate in result.child_aid_estimates.values()
            ],
            parametres_provisoires=list(result.provisional_parameters),
            avertissement=WARNING_TEXT,
            positionnement=_build_positioning(positioning),
        )
