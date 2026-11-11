"""Net balance service: glue between the pure net balance module and the API.

The calculation is stateless and read-only: this service writes nothing to the
database or to a cache, and no logger is used on the calculation path so that
entered values can never leak into logs. Error messages never echo the entered
values. Persistence of the resulting verdict is not done here: the API router
records it through ``NetBalanceVerdictService`` only when the user is logged in
and the calculation succeeded.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from decimal import Decimal

from app.economics.net_balance import compute_net_balance, load_net_balance_parameters
from app.economics.net_balance.balance import NetBalanceResult, Verdict
from app.economics.net_balance.non_reducible import (
    Line,
    NonReducibleParameterError,
    NonReducibleTaxes,
)
from app.economics.net_balance.parameters import NetBalanceParameterRow, NetBalanceParameters
from app.economics.net_balance.types import Status
from app.economics.net_balance.validation import FieldError, parse_input
from app.economics.population_deciles import load_decile_table
from app.economics.positioning import Positioning, compute_positioning
from app.economics.types import Confidence, ModelSnapshot
from app.schemas.net_balance import (
    AidEstimateOut,
    ConsommationNonReductibleOut,
    ConsumptionOut,
    LigneNonReductibleOut,
    NetBalanceRequest,
    NetBalanceResponse,
    PositionnementOut,
    SourceOut,
    StatusChargesOut,
    TrancheOut,
)
from app.services.simulation_service import SnapshotProvider
from app.web.formatting import format_eur_fr, format_number_fr

__all__ = ["NetBalanceService", "NetBalanceUnavailableError", "NetBalanceValidationError"]

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

_MISSING_PARAMETER_MESSAGE = (
    "Le calcul est momentanément indisponible : un paramètre nécessaire à la "
    "consommation non réductible (carburant) est manquant. Réessayez plus tard."
)

_UNKNOWN_CATEGORY_MESSAGE = (
    "Catégorie de consommation inconnue : utilisez uniquement les catégories proposées."
)


class NetBalanceValidationError(ValueError):
    """Invalid request; the router converts it to a 400 response."""

    def __init__(self, errors: list[FieldError]) -> None:
        super().__init__("Requête invalide")
        self.errors = errors


class NetBalanceUnavailableError(RuntimeError):
    """A parameter required by the calculation is missing; the router answers 503."""

    code = "parametre_manquant"

    def __init__(self, message: str = _MISSING_PARAMETER_MESSAGE) -> None:
        super().__init__(message)
        self.message = message


_SOURCE_RE = re.compile(r"^(?P<label>.*?)\s*\((?P<url>https?://[^\s()]+)\)\s*$")

_LINE_LABELS: dict[Line, str] = {
    Line.NOURRITURE: "Frais de nourriture",
    Line.ELECTRICITE: "Électricité",
    Line.ESSENCE: "Frais essence pour voiture",
}


def _fr(value: Decimal) -> str:
    return format_eur_fr(value, places=2)


def _rate_pct_fr(rate: Decimal) -> str:
    """Rate as a French percentage without trailing zeros: ``20 %``, ``6,975 %``."""
    pct = (rate * Decimal(100)).normalize()
    places = max(0, -pct.as_tuple().exponent)  # type: ignore[operator]
    return f"{format_number_fr(pct, places)} %"


def _parse_source(row: NetBalanceParameterRow) -> SourceOut:
    """Split the CSV ``source`` column ``Libellé (URL)``; without URL, label only."""
    match = _SOURCE_RE.match(row.source.strip())
    if match is None or not match.group("label"):
        return SourceOut(libelle=row.source.strip(), url=None, annee=row.year)
    return SourceOut(libelle=match.group("label"), url=match.group("url"), annee=row.year)


def _dedupe_sources(rows: tuple[NetBalanceParameterRow, ...]) -> list[SourceOut]:
    seen: set[tuple[str, str | None, int | None]] = set()
    sources: list[SourceOut] = []
    for row in rows:
        source = _parse_source(row)
        key = (source.libelle, source.url, source.annee)
        if key not in seen:
            seen.add(key)
            sources.append(source)
    return sources


def _build_non_reducible(
    taxes: NonReducibleTaxes,
    params: NetBalanceParameters,
    requested: Mapping[Line, Decimal | None],
    snapshot: ModelSnapshot | None,
) -> ConsommationNonReductibleOut:
    nr = params.non_reducible
    lines: list[LigneNonReductibleOut] = []
    for line in Line:
        t = taxes.lines[line]
        rate_rows = {
            Line.NOURRITURE: nr.food_rate.rows,
            Line.ELECTRICITE: nr.electricity_rate.rows,
            Line.ESSENCE: (nr.fuel_vat_row,),
        }[line]
        used: list[NetBalanceParameterRow] = []
        if requested.get(line) is None:
            used.append(nr.default_rows[line])
        if t.amount > 0:
            used.extend(rate_rows)
        provisional = any(r.confidence == Confidence.PROVISIONAL for r in used)
        if line is Line.ESSENCE and t.amount > 0 and snapshot is not None:
            provisional = provisional or any(
                snapshot.param(name).confidence == Confidence.PROVISIONAL
                for name in taxes.used_fuel_parameters
            )
        default = nr.default_amounts[line]
        lines.append(
            LigneNonReductibleOut(
                ligne=line.value,
                libelle=_LINE_LABELS[line],
                montant=t.amount,
                montant_fr=_fr(t.amount),
                montant_defaut=default,
                montant_defaut_fr=_fr(default),
                taux_tva=t.rate,
                taux_tva_pct_fr=_rate_pct_fr(t.rate),
                tva=t.vat,
                tva_fr=_fr(t.vat),
                accises=t.excise,
                accises_fr=_fr(t.excise),
                total_taxes=t.total,
                total_taxes_fr=_fr(t.total),
                provisoire=provisional,
                sources=_dedupe_sources(nr.sources[line]),
            )
        )
    return ConsommationNonReductibleOut(
        lignes=lines, total_taxes=taxes.total, total_taxes_fr=_fr(taxes.total)
    )


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
        except NonReducibleParameterError:
            raise NetBalanceUnavailableError from None
        return self._build_response(
            result,
            params,
            data.adults,
            data.children,
            requested=data.non_reducible,
            snapshot=snapshot,
        )

    @staticmethod
    def _build_response(
        result: NetBalanceResult,
        params: NetBalanceParameters,
        adults: int,
        children: int,
        *,
        requested: Mapping[Line, Decimal | None] | None = None,
        snapshot: ModelSnapshot | None = None,
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
                depense_restante=consumption.remaining_expenditure,
                depense_restante_fr=_fr(consumption.remaining_expenditure),
            ),
            consommation_non_reductible=_build_non_reducible(
                result.non_reducible, params, requested or {}, snapshot
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
            impot_revenus_locatifs=result.rental_income_tax.tax,
            impot_revenus_locatifs_fr=_fr(result.rental_income_tax.tax),
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
