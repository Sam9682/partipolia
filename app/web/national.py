"""Pages SSR du simulateur national et des Régions (français).

Pages : ``/simulateur-national``, ``/simulateur-national/comparer``, ``/mes-scenarios``,
``/scenarios-partages/{token}``, ``/regions`` et ``/regions/{code}``. Les calculs
sont faits côté serveur (Decimal) ; les fragments HTMX renvoient du HTML et les
erreurs (422 / 429 / 503) sont rendues sous forme de fragment avec « Réessayer »,
sans jamais remplacer le formulaire (la saisie est donc conservée).

Un scénario est une hypothèse, jamais une prévision. Aucune ressource externe.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import national_calc_rate_limit, scenario_save_rate_limit
from app.economics.categories import DEFAULT_CATEGORIES
from app.economics.national import NationalScenario
from app.models.roles import Role, has_role
from app.schemas.auth import UserPublic
from app.schemas.national_simulation import (
    MAX_COMPARED_SCENARIOS,
    NationalScenarioRequest,
    NationalScenarioResult,
)
from app.schemas.simulation import FiscalResult, MoneyRange
from app.services.aggregation_service import MIN_COHORT_SIZE
from app.services.national_simulation_service import (
    NationalSimulationService,
    ServiceUnavailableError,
)
from app.services.regional_aggregation_service import (
    RegionalAggregationService,
    RegionNotFoundError,
)
from app.services.saved_scenario_service import (
    SavedScenarioService,
    ScenarioNotFoundError,
    ScenarioValidationError,
)
from app.services.simulation_service import DatabaseSnapshotProvider
from app.web.formatting import format_eur_fr, format_number_fr
from app.web.public import page_context, templates

national_web_router: APIRouter = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
OptionalUser = Annotated[UserPublic | None, Depends(get_current_user_optional)]

CATEGORY_NAMES: dict[str, str] = {c.code: c.name for c in DEFAULT_CATEGORIES}
_CATEGORIES = [{"code": c.code, "name": c.name} for c in DEFAULT_CATEGORIES]


# --------------------------------------------------------------------------- #
# Filtres Jinja (présentation uniquement)
# --------------------------------------------------------------------------- #
def _eur(value: Decimal | int | None) -> str:
    return "—" if value is None else format_eur_fr(value)


def _num(value: Decimal | int | None, places: int = 0) -> str:
    return "—" if value is None else format_number_fr(value, places)


def _pct(value: Decimal | int | None) -> str:
    return "—" if value is None else f"{format_number_fr(value, 2)}\u00a0%"


def _date_fr(value: datetime | date | None) -> str:
    return "—" if value is None else value.strftime("%d/%m/%Y")


templates.env.filters["eur"] = _eur
templates.env.filters["num"] = _num
templates.env.filters["pct"] = _pct
templates.env.filters["date_fr"] = _date_fr


# --------------------------------------------------------------------------- #
# Aides
# --------------------------------------------------------------------------- #
def _bars(rows: list[dict[str, Any]], key: str = "central") -> None:
    """Ajoute ``width`` (0..100) à chaque ligne, proportionnelle à la valeur absolue."""
    biggest = max((abs(Decimal(r[key])) for r in rows if r[key] is not None), default=Decimal(0))
    for row in rows:
        value = row[key]
        row["width"] = (
            0 if value is None or biggest == 0 else int(abs(Decimal(value)) / biggest * 100)
        )


def _money_row(label: str, value: MoneyRange | None, strong: bool = False) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "label": label,
        "low": value.low,
        "central": value.central,
        "high": value.high,
        "strong": strong,
    }


def component_rows(fiscal: FiscalResult) -> list[dict[str, Any]]:
    """Composantes de l'impact (graphique + tableau équivalent)."""
    candidates = [
        _money_row("Direct : TVA et taxes carburant", fiscal.direct),
        _money_row("Indirect : fournisseurs", fiscal.indirect),
        _money_row("Induit : emploi", fiscal.induced),
        _money_row("Pertes de recettes brutes", fiscal.gross_revenue_loss, True),
        _money_row("Dépenses publiques supplémentaires", fiscal.additional_public_expenditure),
        _money_row("Effet de rebond", fiscal.rebound_offset),
        _money_row("Impact net sur les finances publiques", fiscal.net_public_impact, True),
    ]
    rows = [r for r in candidates if r is not None]
    _bars(rows)
    return rows


def _parse_decimal(raw: str, label: str) -> Decimal:
    try:
        value = Decimal(raw.strip().replace(",", ".").replace("\u00a0", ""))
    except InvalidOperation as exc:
        raise ValueError(f"{label} : saisissez un nombre.") from exc
    if not value.is_finite():
        raise ValueError(f"{label} : saisissez un nombre.")
    return value


def _form_scenario(form: FormData, prefix: str = "") -> NationalScenarioRequest:
    """Construit un scénario validé depuis les champs du formulaire (``ValueError`` sinon)."""
    raw_rate = str(form.get(f"{prefix}participation_rate", "")).strip()
    if not raw_rate:
        raise ValueError("Le taux de participation est obligatoire.")
    rate = _parse_decimal(raw_rate, "Taux de participation")
    reductions: dict[str, Decimal] = {}
    for category in _CATEGORIES:
        raw = str(form.get(f"{prefix}red_{category['code']}", "")).strip()
        if raw:
            reductions[category["code"]] = _parse_decimal(raw, f"Réduction « {category['name']} »")
    horizon = str(form.get(f"{prefix}horizon", "medium") or "medium")
    try:
        return NationalScenarioRequest.model_validate(
            {"participation_rate": rate, "reductions": reductions, "horizon": horizon}
        )
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first.get("loc", ()))
        message = str(first.get("msg", "valeur invalide")).removeprefix("Value error, ")
        raise ValueError(f"Paramètre invalide ({loc}) : {message}") from exc


def _engine(request: NationalScenarioRequest) -> NationalScenario:
    return NationalScenario(
        participation_rate=request.participation_rate,
        reductions=dict(request.reductions),
        horizon=request.horizon,
    )


def _error(request: Request, message: str, code: int, retry_target: str) -> HTMLResponse:
    """Fragment d'erreur avec « Réessayer » ; le formulaire n'est pas remplacé."""
    return templates.TemplateResponse(
        request,
        "public/partials/national_error.html",
        {"message": message, "retry_target": retry_target},
        status_code=code,
    )


def _is_citizen(user: UserPublic | None) -> bool:
    return user is not None and has_role(user.role, Role.CITIZEN)


def _simulation_service(session: AsyncSession) -> NationalSimulationService:
    return NationalSimulationService(DatabaseSnapshotProvider(session), session)


def _meta() -> dict[str, Any]:
    return {
        "min_cohort": MIN_COHORT_SIZE,
        "calculated_on": datetime.now(UTC),
        "category_names": CATEGORY_NAMES,
    }


# --------------------------------------------------------------------------- #
# Simulateur national
# --------------------------------------------------------------------------- #
@national_web_router.get(
    "/simulateur-national",
    response_class=HTMLResponse,
    name="simulateur_national",
    include_in_schema=False,
)
async def national_simulator(request: Request, session: SessionDep) -> HTMLResponse:
    presets = _simulation_service(session).presets()
    return templates.TemplateResponse(
        request,
        "public/national_simulator.html",
        page_context(
            request,
            categories=_CATEGORIES,
            presets=[p.model_dump(mode="json") for p in presets],
            min_cohort=MIN_COHORT_SIZE,
        ),
    )


@national_web_router.post(
    "/simulateur-national/calcul",
    response_class=HTMLResponse,
    name="simulateur_national_calcul",
    include_in_schema=False,
    dependencies=[Depends(national_calc_rate_limit)],
)
async def national_calculate(request: Request, session: SessionDep) -> HTMLResponse:
    form = await request.form()
    try:
        scenario = _form_scenario(form)
    except ValueError as exc:
        return _error(request, str(exc), 422, "form-scenario")
    service = _simulation_service(session)
    try:
        result = await service.calculate(_engine(scenario))
        mobilisation = await service.mobilisation(scenario.participation_rate)
    except ServiceUnavailableError:
        return _error(
            request, "Le simulateur national est momentanément indisponible.", 503, "form-scenario"
        )
    return templates.TemplateResponse(
        request,
        "public/partials/national_result.html",
        {
            "result": result,
            "components": component_rows(result.fiscal),
            "mobilisation": mobilisation,
            **_meta(),
        },
    )


@national_web_router.post(
    "/simulateur-national/enregistrer",
    response_class=HTMLResponse,
    name="simulateur_national_enregistrer",
    include_in_schema=False,
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def national_save(
    request: Request, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    if not _is_citizen(current_user):
        return templates.TemplateResponse(
            request, "public/partials/national_login_required.html", {}
        )
    assert current_user is not None
    form = await request.form()
    try:
        scenario = _form_scenario(form)
    except ValueError as exc:
        return _error(request, str(exc), 422, "form-scenario")
    try:
        saved = await SavedScenarioService(session).save(
            current_user.id, str(form.get("name", "")), _engine(scenario)
        )
    except ScenarioValidationError as exc:
        return _error(request, f"Enregistrement impossible : {exc}", 422, "form-scenario")
    except ServiceUnavailableError:
        return _error(
            request, "Le simulateur national est momentanément indisponible.", 503, "form-scenario"
        )
    return templates.TemplateResponse(
        request, "public/partials/national_saved_ok.html", {"saved": saved}
    )


# --------------------------------------------------------------------------- #
# Comparaison
# --------------------------------------------------------------------------- #
@national_web_router.get(
    "/simulateur-national/comparer",
    response_class=HTMLResponse,
    name="simulateur_national_comparer",
    include_in_schema=False,
)
async def national_compare_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/national_compare.html",
        page_context(
            request,
            categories=_CATEGORIES,
            slots=list(range(1, MAX_COMPARED_SCENARIOS + 1)),
            min_cohort=MIN_COHORT_SIZE,
        ),
    )


@national_web_router.post(
    "/simulateur-national/comparer/calcul",
    response_class=HTMLResponse,
    name="simulateur_national_comparer_calcul",
    include_in_schema=False,
    dependencies=[Depends(national_calc_rate_limit)],
)
async def national_compare_calculate(request: Request, session: SessionDep) -> HTMLResponse:
    form = await request.form()
    scenarios: list[NationalScenarioRequest] = []
    labels: list[str] = []
    try:
        for slot in range(1, MAX_COMPARED_SCENARIOS + 1):
            prefix = f"s{slot}_"
            if not str(form.get(f"{prefix}participation_rate", "")).strip():
                continue
            try:
                scenarios.append(_form_scenario(form, prefix))
            except ValueError as exc:
                raise ValueError(f"Scénario {slot} : {exc}") from exc
            labels.append(f"Scénario {slot}")
    except ValueError as exc:
        return _error(request, str(exc), 422, "form-compare")
    if len(scenarios) < 2:
        return _error(request, "Renseignez au moins 2 scénarios (maximum 4).", 422, "form-compare")
    try:
        comparison = await _simulation_service(session).compare([_engine(s) for s in scenarios])
    except ServiceUnavailableError:
        return _error(
            request, "Le simulateur national est momentanément indisponible.", 503, "form-compare"
        )
    columns: list[dict[str, Any]] = []
    for label, result in zip(labels, comparison.results, strict=True):
        columns.append({"label": label, "result": result})
    chart = [
        {"label": c["label"], "central": c["result"].fiscal.net_public_impact.central}
        for c in columns
    ]
    _bars(chart)
    return templates.TemplateResponse(
        request,
        "public/partials/national_compare_result.html",
        {"columns": columns, "chart": chart, **_meta()},
    )


# --------------------------------------------------------------------------- #
# Scénarios enregistrés (citoyen) et vue partagée (anonyme, lecture seule)
# --------------------------------------------------------------------------- #
@national_web_router.get(
    "/mes-scenarios",
    response_class=HTMLResponse,
    name="mes_scenarios",
    include_in_schema=False,
)
async def saved_page(
    request: Request, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    scenarios: list[Any] = []
    authenticated = _is_citizen(current_user)
    if authenticated:
        assert current_user is not None
        scenarios = await SavedScenarioService(session).list(current_user.id)
    return templates.TemplateResponse(
        request,
        "public/national_saved.html",
        page_context(request, scenarios=scenarios, authenticated=authenticated),
    )


@national_web_router.get(
    "/mes-scenarios/{scenario_id}/resultat",
    response_class=HTMLResponse,
    name="mes_scenarios_resultat",
    include_in_schema=False,
    dependencies=[Depends(national_calc_rate_limit)],
)
async def saved_result(
    request: Request, scenario_id: int, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    if not _is_citizen(current_user):
        return templates.TemplateResponse(
            request, "public/partials/national_login_required.html", {}
        )
    assert current_user is not None
    try:
        result = await SavedScenarioService(session).get(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scénario introuvable.") from exc
    except ServiceUnavailableError:
        return _error(
            request, "Le simulateur national est momentanément indisponible.", 503, "saved-list"
        )
    return _result_response(request, result)


@national_web_router.post(
    "/mes-scenarios/{scenario_id}/partager",
    response_class=HTMLResponse,
    name="mes_scenarios_partager",
    include_in_schema=False,
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def saved_share(
    request: Request, scenario_id: int, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    if not _is_citizen(current_user):
        return templates.TemplateResponse(
            request, "public/partials/national_login_required.html", {}
        )
    assert current_user is not None
    try:
        link = await SavedScenarioService(session).share(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scénario introuvable.") from exc
    url = str(request.url_for("scenario_partage", token=link.token))
    return templates.TemplateResponse(
        request, "public/partials/national_share_link.html", {"url": url}
    )


@national_web_router.post(
    "/mes-scenarios/{scenario_id}/revoquer",
    response_class=HTMLResponse,
    name="mes_scenarios_revoquer",
    include_in_schema=False,
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def saved_revoke(
    request: Request, scenario_id: int, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    if not _is_citizen(current_user):
        return templates.TemplateResponse(
            request, "public/partials/national_login_required.html", {}
        )
    assert current_user is not None
    try:
        await SavedScenarioService(session).revoke_share(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scénario introuvable.") from exc
    return HTMLResponse('<p role="status">Le lien de partage est révoqué.</p>')


@national_web_router.post(
    "/mes-scenarios/{scenario_id}/supprimer",
    response_class=HTMLResponse,
    name="mes_scenarios_supprimer",
    include_in_schema=False,
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def saved_delete(
    request: Request, scenario_id: int, session: SessionDep, current_user: OptionalUser
) -> HTMLResponse:
    if not _is_citizen(current_user):
        return templates.TemplateResponse(
            request, "public/partials/national_login_required.html", {}
        )
    assert current_user is not None
    try:
        await SavedScenarioService(session).delete(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scénario introuvable.") from exc
    return HTMLResponse("")  # la ligne est retirée par HTMX (hx-swap="outerHTML")


@national_web_router.get(
    "/scenarios-partages/{token}",
    response_class=HTMLResponse,
    name="scenario_partage",
    include_in_schema=False,
    dependencies=[Depends(national_calc_rate_limit)],
)
async def shared_page(request: Request, token: str, session: SessionDep) -> HTMLResponse:
    try:
        result = await SavedScenarioService(session).get_shared(token)
    except ScenarioNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Scénario introuvable.") from exc
    except ServiceUnavailableError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Le simulateur national est momentanément indisponible.",
        ) from exc
    return templates.TemplateResponse(
        request,
        "public/national_shared.html",
        page_context(
            request,
            result=result,
            components=component_rows(result.fiscal),
            **_meta(),
        ),
    )


def _result_response(request: Request, result: NationalScenarioResult) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/partials/national_result.html",
        {
            "result": result,
            "components": component_rows(result.fiscal),
            "mobilisation": None,
            **_meta(),
        },
    )


# --------------------------------------------------------------------------- #
# Régions
# --------------------------------------------------------------------------- #
@national_web_router.get(
    "/regions", response_class=HTMLResponse, name="regions", include_in_schema=False
)
async def regions_page(request: Request, session: SessionDep) -> HTMLResponse:
    listing = await RegionalAggregationService(session).list_regions()
    rows = [
        {
            "code": v.code,
            "label": v.label,
            "published": v.status == "published",
            "participants": v.participants,
            "central": v.fiscal.net_public_impact.central if v.fiscal else None,
        }
        for v in listing.regions
    ]
    chart = [r for r in rows if r["published"] and r["central"] is not None]
    _bars(chart)
    return templates.TemplateResponse(
        request,
        "public/regions.html",
        page_context(
            request,
            rows=rows,
            chart=chart,
            min_cohort=listing.min_cohort,
            withheld_count=sum(1 for r in rows if not r["published"]),
        ),
    )


@national_web_router.get(
    "/regions/{code}", response_class=HTMLResponse, name="region_detail", include_in_schema=False
)
async def region_detail_page(request: Request, code: str, session: SessionDep) -> HTMLResponse:
    service = RegionalAggregationService(session)
    try:
        view = await service.detail(code)
        history = await service.history(code)
    except RegionNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Région inconnue.") from exc
    points = [
        {
            "date": p.date,
            "participants": p.participants,
            "central": p.net_public_impact,
            "personal_savings": p.personal_savings,
        }
        for p in history.points
    ]
    _bars(points)
    categories = [
        {
            "label": CATEGORY_NAMES.get(c.code, "Autres catégories"),
            "participants": c.participants,
            "central": c.avoided_expenditure,
        }
        for c in view.categories
    ]
    _bars(categories)
    return templates.TemplateResponse(
        request,
        "public/region_detail.html",
        page_context(
            request,
            view=view,
            components=component_rows(view.fiscal) if view.fiscal else [],
            points=points,
            categories=categories,
            min_cohort=view.min_cohort,
        ),
    )
