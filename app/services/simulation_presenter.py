"""Maps engine outcomes to API schemas (pure: no database, no web framework)."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.economics.decimal_utils import ZERO, money
from app.economics.engine import EngineOutcome, ScenarioOutcome, run_engine
from app.economics.parameters import MODULE_REQUIREMENTS
from app.economics.personal import compute_personal_savings
from app.economics.types import SCENARIOS, Confidence, ModelSnapshot
from app.schemas.simulation import (
    CategoryResult,
    DirectFiscalResult,
    FiscalResult,
    MoneyRange,
    PersonalResult,
    PublicSectorBreakdown,
    SectorImpact,
    SimulationRequest,
    SimulationResponse,
)

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

_CONFIDENCE_ORDER = (
    Confidence.HIGH,
    Confidence.MEDIUM,
    Confidence.LOW,
    Confidence.PROVISIONAL,
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
    )


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
    )
