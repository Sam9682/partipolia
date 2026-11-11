"""Maps engine outcomes to API schemas (pure: no database, no web framework)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.core.logging import get_logger
from app.economics.decimal_utils import ZERO, money
from app.economics.energy_baseline import EnergyBaseline, EnergyLine, compute_energy_baseline
from app.economics.engine import EngineOutcome, ScenarioOutcome, run_engine
from app.economics.parameters import (
    ENERGY_BASELINE_REQUIREMENTS,
    MODULE_REQUIREMENTS,
    energy_baseline_available,
)
from app.economics.personal import compute_personal_savings
from app.economics.tax_breakdown import PROPERTY_TAX_SHARE, TAX_TYPES, project_scenario
from app.economics.types import SCENARIOS, Confidence, ModelSnapshot
from app.schemas.simulation import (
    CategoryResult,
    DirectFiscalResult,
    EnergyBaselineOut,
    EnergyLineOut,
    FiscalCentral,
    FiscalResult,
    MoneyRange,
    PersonalResult,
    PublicSectorBreakdown,
    SectorImpact,
    SimulationRequest,
    SimulationResponse,
    TaxBreakdown,
    TaxBreakdownLine,
)
from app.web.formatting import format_eur_fr

logger = get_logger(__name__)

DISCLAIMERS: tuple[str, ...] = (
    "Ces montants sont des estimations modélisées, et non des mesures de recettes fiscales réelles.",
    "Les économies personnelles, le chiffre d'affaires évité, la valeur ajoutée, les recettes publiques "
    "et les dépenses publiques supplémentaires sont des indicateurs distincts.",
    "Les fourchettes reflètent l'incertitude des paramètres ; plusieurs paramètres sont encore "
    "provisoires (voir la méthodologie).",
    "Votre engagement est déclaratif : il n'est pas vérifié par la plateforme.",
)

METHODOLOGY_DIRECT = (
    "TVA = dépense TTC × taux / (1 + taux), calculée par taux applicable à chaque catégorie. "
    "Les taux légaux sont officiels ; la répartition des dépenses entre taux est une hypothèse "
    "paramétrable dont le niveau de confiance est indiqué."
)

METHODOLOGY_FISCAL = (
    "Direct : TVA sur les achats évités et taxes sur les carburants évités. Indirect : modèle "
    "entrées-sorties de Leontief (INSEE, niveau A38), limité aux impôts de production et à l'impôt "
    "sur les sociétés, sans double compte avec la TVA ni avec les prélèvements sur le travail. "
    "Induit : effet sur l'emploi par élasticités sectorielles (cotisations, impôt sur le revenu, "
    "allocations chômage et activité partielle). Net = pertes de recettes + dépenses publiques "
    "supplémentaires − effet de rebond (réaffectation d'une partie des économies)."
)

METHODOLOGY_BREAKDOWN = (
    "Ventilation du manque à gagner brut par type de prélèvement, issue des mêmes composantes du "
    "modèle que les montants Direct, Indirect et Induit : la somme des lignes correspond à la perte "
    "de recettes brute (à quelques centimes d'arrondi près). Les prestations chômage, l'activité "
    "partielle et l'effet de rebond ne sont pas des prélèvements et sont exclus. La taxe foncière "
    "est isolée en appliquant une part paramétrable aux impôts de production ; sans ce paramètre, "
    "elle est indiquée comme indisponible et les impôts de production sont comptés en totalité."
)

#: Parameter types examined to decide whether a breakdown line is provisional.
_LINE_PARAMETER_TYPES: dict[str, tuple[str, ...]] = {
    "tva": ("vat_split",),
    "accises": (
        "fuel_excise_per_l",
        "fuel_l_per_100km",
        "fuel_price_ttc_per_l",
        "car_share",
        "km_per_trip",
        "trips_per_1000_eur",
    ),
    "charges_salariales": ("employee_contribution_rate", "employment_elasticity"),
    "charges_patronales": ("branch_compensation", "branch_wages", "employment_elasticity"),
    "impot_revenu": ("income_tax_rate_on_wages", "employment_elasticity"),
    "impots_production": ("production_tax_rate_on_va", "demand_share", "io_flow"),
    "taxe_fonciere": ("production_tax_rate_on_va", PROPERTY_TAX_SHARE),
    "impot_societes": ("corporate_tax_rate", "ebe_to_taxable_profit"),
}

_CONFIDENCE_ORDER = (
    Confidence.HIGH,
    Confidence.MEDIUM,
    Confidence.LOW,
    Confidence.PROVISIONAL,
)


METHODOLOGY_ENERGY = (
    "Moyenne nationale par adulte : dépense TTC = litres × prix TTC par litre ; accise = litres × "
    "accise par litre ; TVA = dépense TTC × taux / (1 + taux). Ces montants décrivent une "
    "consommation moyenne, ne dépendent pas de vos saisies et ne sont pas inclus dans vos "
    "économies."
)


class UnknownCategoryError(ValueError):
    """Raised when a request references a category that does not exist."""


def _range(outcome: EngineOutcome, getter: Callable[[ScenarioOutcome], Decimal]) -> MoneyRange:
    return MoneyRange(
        low=getter(outcome.low), central=getter(outcome.central), high=getter(outcome.high)
    )


def _optional_range(
    outcome: EngineOutcome,
    getter: Callable[[ScenarioOutcome], Decimal | None],
    enabled: bool,
    *,
    sort: bool = False,
) -> MoneyRange | None:
    """Range over the three scenarios, or ``None`` if the module is disabled.

    With ``sort=True`` the range is reported as (min, central, max). It is used for
    non-fiscal indicators (turnover, value added, import leakage, fuel savings),
    which are not monotonic in the scenario: a low-*tax* scenario embeds less VAT in
    prices and therefore shows a *larger* turnover excluding VAT.
    """
    if not enabled:
        return None
    values = [getter(outcome.scenario(s)) or ZERO for s in SCENARIOS]
    if sort:
        return MoneyRange(low=min(values), central=values[1], high=max(values))
    return MoneyRange(low=values[0], central=values[1], high=values[2])


def _lowest_confidence(values: list[Confidence]) -> Confidence:
    return max(values, key=_CONFIDENCE_ORDER.index) if values else Confidence.HIGH


def _used_confidence(
    snapshot: ModelSnapshot, modules: dict[str, bool], active_categories: set[str]
) -> tuple[Confidence, list[str]]:
    """Lowest confidence among the parameters actually used, and the provisional ones."""
    used_types: set[str] = {"vat_split"}
    for module, enabled in modules.items():
        if enabled:
            used_types.update(MODULE_REQUIREMENTS[module])
    confidences: list[Confidence] = []
    provisional: set[str] = set()
    for (ptype, category, _key), parameter in snapshot.parameters.items():
        if ptype not in used_types:
            continue
        if category is not None and category not in active_categories:
            continue
        confidences.append(parameter.confidence)
        if parameter.confidence is Confidence.PROVISIONAL:
            provisional.add(ptype)
    return _lowest_confidence(confidences), sorted(provisional)


def _property_share(snapshot: ModelSnapshot) -> Decimal | None:
    """Central value of the optional property-tax share, or ``None`` if absent."""
    if not snapshot.has(PROPERTY_TAX_SHARE):
        return None
    return snapshot.param(PROPERTY_TAX_SHARE).central


def build_tax_breakdown(snapshot: ModelSnapshot, outcome: EngineOutcome) -> TaxBreakdown:
    """Breakdown of the gross revenue loss by tax type (8 lines, fixed order)."""
    share = _property_share(snapshot)
    per_scenario = {s: project_scenario(outcome.scenario(s), share) for s in SCENARIOS}
    provisional_types = {
        ptype
        for (ptype, _category, _key), parameter in snapshot.parameters.items()
        if parameter.confidence is Confidence.PROVISIONAL
    }
    fuel_enabled = outcome.modules.get("fuel", False)
    lines: list[TaxBreakdownLine] = []
    used_provisional: set[str] = set()
    for tax_id, label in TAX_TYPES:
        types = set(_LINE_PARAMETER_TYPES[tax_id])
        if tax_id == "tva" and fuel_enabled:
            types.add("fuel_vat_rate")
        line_provisional = types & provisional_types
        used_provisional |= line_provisional
        lines.append(
            TaxBreakdownLine(
                id=tax_id,
                label=label,
                amount=MoneyRange(
                    low=per_scenario["low"].amounts[tax_id],
                    central=per_scenario["central"].amounts[tax_id],
                    high=per_scenario["high"].amounts[tax_id],
                ),
                available=per_scenario["central"].available[tax_id],
                provisional=bool(line_provisional),
            )
        )
    total = MoneyRange(
        low=sum((per_scenario["low"].amounts[t] for t, _ in TAX_TYPES), ZERO),
        central=sum((per_scenario["central"].amounts[t] for t, _ in TAX_TYPES), ZERO),
        high=sum((per_scenario["high"].amounts[t] for t, _ in TAX_TYPES), ZERO),
    )
    return TaxBreakdown(
        lines=lines,
        total=total,
        provisional_parameters=sorted(used_provisional),
        note=METHODOLOGY_BREAKDOWN,
    )


def build_fiscal_result(
    snapshot: ModelSnapshot,
    outcome: EngineOutcome,
    active_categories: set[str],
    horizon: str,
) -> FiscalResult:
    """Fiscal block of an engine outcome (shared by the simulator and the dashboard)."""
    modules = dict(outcome.modules)
    overall, provisional = _used_confidence(snapshot, modules, active_categories)

    def sectors(name: str) -> MoneyRange:
        values = [money(outcome.scenario(s).by_public_sector()[name]) for s in SCENARIOS]
        return MoneyRange(low=values[0], central=values[1], high=values[2])

    top: list[SectorImpact] = []
    if outcome.central.indirect:
        indirect = outcome.central.indirect
        ranked = sorted(indirect.output_change.items(), key=lambda kv: kv[1], reverse=True)[:8]
        top = [
            SectorImpact(
                code=code,
                turnover_change=change,
                value_added_change=indirect.value_added_change.get(code, ZERO),
            )
            for code, change in ranked
        ]
    try:
        tax_breakdown: TaxBreakdown | None = build_tax_breakdown(snapshot, outcome)
    except Exception:  # noqa: BLE001 - the breakdown must never break the fiscal result
        logger.exception("tax_breakdown_failed")  # no input values are logged
        tax_breakdown = None
    return FiscalResult(
        horizon=horizon,
        modules=modules,
        direct=_range(outcome, lambda s: s.direct_total),
        indirect=_optional_range(outcome, lambda s: s.indirect_total, modules["indirect"]),
        induced=_optional_range(outcome, lambda s: s.induced_total, modules["induced"]),
        gross_revenue_loss=_range(outcome, lambda s: s.gross_revenue_loss),
        additional_public_expenditure=_range(outcome, lambda s: s.additional_public_expenditure),
        rebound_offset=_range(outcome, lambda s: s.rebound_offset),
        net_public_impact=_range(outcome, lambda s: s.net_public_impact),
        reduced_turnover=_optional_range(
            outcome,
            lambda s: s.indirect.turnover_change if s.indirect else None,
            modules["indirect"],
            sort=True,
        ),
        reduced_value_added=_optional_range(
            outcome,
            lambda s: s.indirect.value_added_total if s.indirect else None,
            modules["indirect"],
            sort=True,
        ),
        household_fuel_savings=_optional_range(
            outcome,
            lambda s: s.fuel.household_fuel_savings if s.fuel else None,
            modules["fuel"],
            sort=True,
        ),
        import_leakage=_optional_range(
            outcome,
            lambda s: s.indirect.import_leakage if s.indirect else None,
            modules["indirect"],
            sort=True,
        ),
        by_public_sector=PublicSectorBreakdown(
            central=sectors("central"),
            local=sectors("local"),
            social_security=sectors("social_security"),
        ),
        top_sectors=top,
        confidence=overall.value,
        provisional_parameters=provisional,
        methodology=METHODOLOGY_FISCAL,
        tax_breakdown=tax_breakdown,
    )


def _energy_line_out(line: EnergyLine) -> EnergyLineOut:
    return EnergyLineOut(
        litres=format(line.litres, "f"),
        spend_ttc=format(line.spend_ttc, "f"),
        excise=format(line.excise, "f"),
        vat=format(line.vat, "f"),
    )


def build_energy_result(snapshot: ModelSnapshot, baseline: EnergyBaseline) -> EnergyBaselineOut:
    """Energy block: source and confidence come from the parameters actually used."""
    used = [snapshot.param(ptype) for ptype in ENERGY_BASELINE_REQUIREMENTS]
    notes = sorted({p.note for p in used if p.note})
    return EnergyBaselineOut(
        fuel=_energy_line_out(baseline.fuel),
        heating_oil=_energy_line_out(baseline.heating_oil),
        total=_energy_line_out(baseline.total),
        source=" | ".join(notes),
        model_version=snapshot.model_version,
        confidence=_lowest_confidence([p.confidence for p in used]).value,
        methodology=METHODOLOGY_ENERGY,
    )


def _central_of(outcome: EngineOutcome) -> FiscalCentral:
    """Central values of the table components (same accessors as ``build_fiscal_result``)."""
    modules = dict(outcome.modules)
    central = outcome.central
    return FiscalCentral(
        direct=central.direct_total,
        indirect=central.indirect_total if modules["indirect"] else None,
        induced=central.induced_total if modules["induced"] else None,
        gross_revenue_loss=central.gross_revenue_loss,
        additional_public_expenditure=central.additional_public_expenditure,
        rebound_offset=central.rebound_offset,
        net_public_impact=central.net_public_impact,
    )


def build_full_reduction_scenario(
    snapshot: ModelSnapshot, request: SimulationRequest
) -> FiscalCentral:
    """Central fiscal values if every category were reduced by 100 %.

    The request reductions are ignored; adults, reference overrides and horizon are kept.
    Engine errors propagate (no partial result).
    """
    all_full = {code: Decimal(100) for code in snapshot.category_codes}
    savings = compute_personal_savings(
        snapshot, all_full, request.adults, request.reference_overrides
    )
    return _central_of(run_engine(snapshot, savings, request.horizon))


def build_response(snapshot: ModelSnapshot, request: SimulationRequest) -> SimulationResponse:
    unknown = (set(request.reductions) | set(request.reference_overrides)) - set(
        snapshot.category_codes
    )
    if unknown:
        raise UnknownCategoryError(sorted(unknown)[0])

    overrides = request.reference_overrides
    savings = compute_personal_savings(snapshot, request.reductions, request.adults, overrides)
    outcome = run_engine(snapshot, savings, request.horizon)
    modules = dict(outcome.modules)

    # The model is linear, so each category's contribution is computed exactly by
    # running the engine on that category alone (the contributions add up).
    per_category: dict[str, EngineOutcome] = {}
    for row in savings.categories:
        if row.reduction_percentage > 0:
            single_savings = compute_personal_savings(
                snapshot, {row.code: row.reduction_percentage}, request.adults, overrides
            )
            per_category[row.code] = run_engine(snapshot, single_savings, request.horizon)

    def direct_vat(o: EngineOutcome, code: str) -> MoneyRange:
        values = [
            next(c for c in o.scenario(s).direct.categories if c.code == code).vat
            for s in SCENARIOS
        ]
        return MoneyRange(low=values[0], central=values[1], high=values[2])

    def direct_ht(o: EngineOutcome, code: str) -> MoneyRange:
        values = [
            next(c for c in o.scenario(s).direct.categories if c.code == code).turnover_ht
            for s in SCENARIOS
        ]
        return MoneyRange(low=values[0], central=values[1], high=values[2])

    categories: list[CategoryResult] = []
    active: set[str] = set()
    confidence_list: list[Confidence] = []
    for row in savings.categories:
        profile = snapshot.vat_profiles[row.code]
        single = per_category.get(row.code)
        if row.reduction_percentage > 0:
            active.add(row.code)
            confidence_list.append(profile.confidence)
        zero = MoneyRange(low=ZERO, central=ZERO, high=ZERO)
        categories.append(
            CategoryResult(
                code=row.code,
                name=snapshot.category(row.code).name,
                reduction_percentage=row.reduction_percentage,
                original_expenditure=row.original_expenditure,
                avoided_expenditure=row.avoided_expenditure,
                remaining_expenditure=row.remaining_expenditure,
                annual_savings=row.annual_savings,
                direct_vat=direct_vat(single or outcome, row.code) if single else zero,
                turnover_ht=direct_ht(single or outcome, row.code) if single else zero,
                vat_confidence=profile.confidence.value,
                indirect=_optional_range(single, lambda s: s.indirect_total, modules["indirect"])
                if single
                else None,
                induced=_optional_range(single, lambda s: s.induced_total, modules["induced"])
                if single
                else None,
                additional_public_expenditure=_optional_range(
                    single, lambda s: s.additional_public_expenditure, modules["induced"]
                )
                if single
                else None,
                net_public_impact=_range(single, lambda s: s.net_public_impact) if single else zero,
            )
        )

    vat_total = _range(outcome, lambda s: s.direct.vat)
    turnover_values = [outcome.scenario(s).direct.turnover_ht for s in SCENARIOS]
    # Turnover excluding VAT is not monotonic in the scenario (see _optional_range).
    turnover_total = MoneyRange(
        low=min(turnover_values), central=turnover_values[1], high=max(turnover_values)
    )
    fiscal = build_fiscal_result(snapshot, outcome, active, request.horizon)
    energie = (
        build_energy_result(snapshot, compute_energy_baseline(snapshot))
        if energy_baseline_available(snapshot)
        else None
    )
    fiscal_full_reduction = build_full_reduction_scenario(snapshot, request)

    return SimulationResponse(
        model_version=snapshot.model_version,
        dataset_versions=list(snapshot.dataset_versions),
        personal=PersonalResult(
            adults=savings.adults,
            original_expenditure=savings.original_expenditure,
            avoided_expenditure=savings.avoided_expenditure,
            remaining_expenditure=savings.remaining_expenditure,
            annual_savings=savings.annual_savings,
            monthly_savings=savings.monthly_savings,
        ),
        direct=DirectFiscalResult(
            vat=vat_total,
            turnover_ht=turnover_total,
            confidence=_lowest_confidence(confidence_list).value,
            methodology=METHODOLOGY_DIRECT,
        ),
        fiscal=fiscal,
        categories=categories,
        disclaimers=list(DISCLAIMERS),
        energie=energie,
        fiscal_full_reduction=fiscal_full_reduction,
        net_public_impact_central_fr=format_eur_fr(fiscal.net_public_impact.central, places=2),
    )
