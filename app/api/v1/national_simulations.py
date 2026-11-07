"""National scenario simulator endpoints (``/api/v1/national-simulations``).

Guard order on every route: rate limit -> 401 -> role -> ownership (404) ->
validation (422) -> business rule. Route-level dependencies run before the
endpoint's own, and ownership is resolved by the service before a name is
validated, which yields that order. Body validation errors are reported as 422
naming the faulty field.

A scenario is a *hypothesis*, never a forecast. Router mounting is done elsewhere.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_session
from app.core.middleware import error_response
from app.core.rate_limit import national_calc_rate_limit, scenario_save_rate_limit
from app.economics.national import NationalScenario
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.national_simulation import (
    MobilisationGap,
    NationalCompareRequest,
    NationalComparison,
    NationalScenarioRequest,
    NationalScenarioResult,
    SavedScenarioCreate,
    SavedScenarioRename,
    SavedScenarioSummaryOut,
    ScenarioPreset,
    ShareLinkOut,
)
from app.services.national_simulation_service import (
    NationalSimulationService,
    ServiceUnavailableError,
)
from app.services.saved_scenario_service import (
    SavedScenarioService,
    ScenarioNotFoundError,
    ScenarioValidationError,
)
from app.services.simulation_service import DatabaseSnapshotProvider


class _Unprocessable422Route(APIRoute):
    """Reports request validation errors as 422 and names the faulty field."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as exc:
                fields = []
                for err in exc.errors():
                    loc = [str(p) for p in err.get("loc", ()) if p != "body"]
                    fields.append(
                        {
                            "field": ".".join(loc) if loc else "<corps>",
                            "message": str(err.get("msg", "invalide")),
                        }
                    )
                return error_response(
                    status_code=422,
                    message="Les paramètres du scénario sont invalides.",
                    request_id=getattr(request.state, "request_id", None),
                    fields=fields,
                    code="VALIDATION_ERROR",
                )

        return handler


router = APIRouter(route_class=_Unprocessable422Route)
shared_router = APIRouter(route_class=_Unprocessable422Route)

_citizen_guard = require_roles(Role.CITIZEN)
CitizenDep = Annotated[UserPublic, Depends(_citizen_guard)]
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]

_NOT_FOUND = "Scénario introuvable."
_UNAVAILABLE = "Le simulateur national est momentanément indisponible."


def get_national_simulation_service(session: SessionDep) -> NationalSimulationService:
    """Dependency providing the national simulation service (overridable in tests)."""
    return NationalSimulationService(DatabaseSnapshotProvider(session), session)


def get_saved_scenario_service(session: SessionDep) -> SavedScenarioService:
    """Dependency providing the saved scenario service (overridable in tests)."""
    return SavedScenarioService(session)


SimulationServiceDep = Annotated[
    NationalSimulationService, Depends(get_national_simulation_service)
]
SavedServiceDep = Annotated[SavedScenarioService, Depends(get_saved_scenario_service)]


def _to_engine(request: NationalScenarioRequest) -> NationalScenario:
    return NationalScenario(
        participation_rate=request.participation_rate,
        reductions=dict(request.reductions),
        horizon=request.horizon,
    )


def _unavailable(exc: ServiceUnavailableError) -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, _UNAVAILABLE)


def _not_found() -> HTTPException:
    # Same answer for "missing" and "someone else's": nothing leaks.
    return HTTPException(status.HTTP_404_NOT_FOUND, _NOT_FOUND)


def _invalid(exc: ScenarioValidationError) -> HTTPException:
    return HTTPException(422, str(exc))


# --------------------------------------------------------------------------- #
# Public, stateless
# --------------------------------------------------------------------------- #
@router.get(
    "/presets",
    response_model=list[ScenarioPreset],
    summary="Présélections de scénarios nationaux",
)
async def presets(service: SimulationServiceDep) -> list[ScenarioPreset]:
    return service.presets()


@router.post(
    "/calculate",
    response_model=NationalScenarioResult,
    summary="Calculer un scénario national (hypothèse, non prévision)",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def calculate(
    payload: NationalScenarioRequest, service: SimulationServiceDep
) -> NationalScenarioResult:
    try:
        return await service.calculate(_to_engine(payload))
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.post(
    "/compare",
    response_model=NationalComparison,
    summary="Comparer 2 à 4 scénarios nationaux",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def compare(
    payload: NationalCompareRequest, service: SimulationServiceDep
) -> NationalComparison:
    try:
        return await service.compare([_to_engine(s) for s in payload.scenarios])
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.get(
    "/mobilisation",
    response_model=MobilisationGap,
    summary="Écart entre un taux de scénario et la mobilisation déclarée",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def mobilisation(
    service: SimulationServiceDep,
    scenario_rate: Annotated[Decimal, Query(ge=0, le=100, decimal_places=2, allow_inf_nan=False)],
) -> MobilisationGap:
    try:
        return await service.mobilisation(scenario_rate)
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc


# --------------------------------------------------------------------------- #
# Saved scenarios (citizen)
# --------------------------------------------------------------------------- #
@router.post(
    "/saved",
    response_model=SavedScenarioSummaryOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer un scénario national",
    dependencies=[Depends(scenario_save_rate_limit), Depends(_citizen_guard)],
)
async def save_scenario(
    payload: SavedScenarioCreate,
    current_user: CitizenDep,
    service: SavedServiceDep,
) -> SavedScenarioSummaryOut:
    try:
        saved = await service.save(current_user.id, payload.name, _to_engine(payload))
    except ScenarioValidationError as exc:
        raise _invalid(exc) from exc
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc
    return SavedScenarioSummaryOut(
        id=saved.id,
        name=saved.name,
        created_at=saved.created_at,
        net_impact_central=saved.net_impact_central,
        model_version=saved.model_version,
    )


@router.get(
    "/saved",
    response_model=list[SavedScenarioSummaryOut],
    summary="Lister mes scénarios enregistrés",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def list_saved(
    current_user: CitizenDep, service: SavedServiceDep
) -> list[SavedScenarioSummaryOut]:
    rows = await service.list(current_user.id)
    return [
        SavedScenarioSummaryOut(
            id=r.id,
            name=r.name,
            created_at=r.created_at,
            net_impact_central=r.net_impact_central,
            model_version=r.model_version,
        )
        for r in rows
    ]


@router.get(
    "/saved/{scenario_id}",
    response_model=NationalScenarioResult,
    summary="Relire un scénario enregistré (recalculé avec sa version de modèle)",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def get_saved(
    scenario_id: int, current_user: CitizenDep, service: SavedServiceDep
) -> NationalScenarioResult:
    try:
        return await service.get(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc


@router.patch(
    "/saved/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Renommer un scénario enregistré",
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def rename_saved(
    scenario_id: int,
    payload: SavedScenarioRename,
    current_user: CitizenDep,
    service: SavedServiceDep,
) -> Response:
    try:
        await service.rename(current_user.id, scenario_id, payload.name)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    except ScenarioValidationError as exc:
        raise _invalid(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/saved/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer un scénario enregistré",
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def delete_saved(
    scenario_id: int, current_user: CitizenDep, service: SavedServiceDep
) -> Response:
    try:
        await service.delete(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/saved/{scenario_id}/share",
    response_model=ShareLinkOut,
    summary="Créer un lien de partage en lecture seule",
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def share_saved(
    scenario_id: int,
    request: Request,
    current_user: CitizenDep,
    service: SavedServiceDep,
) -> ShareLinkOut:
    try:
        link = await service.share(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    url = str(request.url_for("get_shared_scenario", token=link.token))
    return ShareLinkOut(url=url, token=link.token)


@router.delete(
    "/saved/{scenario_id}/share",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Révoquer le lien de partage",
    dependencies=[Depends(scenario_save_rate_limit)],
)
async def revoke_share(
    scenario_id: int, current_user: CitizenDep, service: SavedServiceDep
) -> Response:
    try:
        await service.revoke_share(current_user.id, scenario_id)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------------- #
# Public read-only shared view (to be mounted at ``/shared-scenarios``)
# --------------------------------------------------------------------------- #
@shared_router.get(
    "/{token}",
    response_model=NationalScenarioResult,
    name="get_shared_scenario",
    summary="Lire un scénario partagé (lecture seule, anonyme)",
    dependencies=[Depends(national_calc_rate_limit)],
)
async def get_shared_scenario(token: str, service: SavedServiceDep) -> NationalScenarioResult:
    try:
        return await service.get_shared(token)
    except ScenarioNotFoundError as exc:
        raise _not_found() from exc
    except ServiceUnavailableError as exc:
        raise _unavailable(exc) from exc


__all__ = ["router", "shared_router"]
