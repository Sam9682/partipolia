"""Net contributor balance endpoints (calculation is anonymous, rate limited).

The calculation itself is stateless and needs no account. When the caller is
logged in, after a successful calculation the resulting verdict and balance are
persisted, along with the status and net income of the first income line (so
that "Mes revenus" is prefilled, read back by ``GET /incomes/me``). Anonymous
visitors never trigger any write, and invalid input leaves stored data untouched.
Nothing else entered (aids, consumption, children, real estate) is stored, and
no entered value is logged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_current_user_optional
from app.core.database import get_session
from app.core.middleware import error_response
from app.core.rate_limit import default_rate_limit
from app.economics.net_balance.validation import parse_input
from app.schemas.auth import UserPublic
from app.schemas.net_balance import NetBalanceRequest, NetBalanceResponse, UserIncomeOut
from app.services.net_balance_service import (
    NetBalanceService,
    NetBalanceUnavailableError,
    NetBalanceValidationError,
)
from app.services.net_balance_verdict_service import NetBalanceVerdictService
from app.services.simulation_service import DatabaseSnapshotProvider
from app.services.user_income_service import UserIncomeService

router = APIRouter()


def get_net_balance_service(
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
) -> NetBalanceService:
    """Dependency providing the net balance service (overridable in tests)."""
    return NetBalanceService(DatabaseSnapshotProvider(session))


@router.post(
    "/calculate",
    response_model=NetBalanceResponse,
    summary="Calculer le bilan contributeur net / bénéficiaire net",
    dependencies=[Depends(default_rate_limit("simulate"))],
    responses={
        status.HTTP_400_BAD_REQUEST: {"description": "Entrée invalide"},
        status.HTTP_503_SERVICE_UNAVAILABLE: {"description": "Paramètre manquant"},
    },
)
async def calculate(
    payload: NetBalanceRequest,
    service: Annotated[NetBalanceService, Depends(get_net_balance_service)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> NetBalanceResponse | JSONResponse:
    """Net balance; no account required, entered values are never logged.

    The calculation is stateless. Only for a logged-in user, and only after a
    successful calculation, the verdict and balance are upserted (one per account),
    along with the status and net income of the first income line (to prefill
    "Mes revenus"). Nothing else entered is stored.
    """
    try:
        response = await service.calculate(payload)
    except NetBalanceValidationError as exc:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message="Le corps de la requête est invalide.",
            request_id=getattr(request.state, "request_id", None),
            fields=[{"field": e.field, "message": e.message} for e in exc.errors],
        )
    except NetBalanceUnavailableError as exc:
        return error_response(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            message=exc.message,
            request_id=getattr(request.state, "request_id", None),
            code=exc.code,
        )
    if current_user is not None:
        await NetBalanceVerdictService(session).record(
            current_user.id, response.verdict, response.bilan
        )
        # Input already validated by the service: re-parsing is pure and cheap.
        data, _ = parse_input(payload)
        if data is not None and data.incomes:
            line = data.incomes[0]
            await UserIncomeService(session).record(
                current_user.id,
                line.status.value,
                line.net_income.quantize(Decimal("0.01")),
            )
    return response


@router.get(
    "/incomes/me",
    response_model=UserIncomeOut,
    summary="Revenus enregistrés de l'utilisateur connecté",
    responses={status.HTTP_401_UNAUTHORIZED: {"description": "Non authentifié"}},
)
async def get_my_incomes(
    response: Response,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
) -> UserIncomeOut:
    """Stored status and net income of the logged-in user (nulls when none)."""
    response.headers["Cache-Control"] = "no-store"
    row = await UserIncomeService(session).get(current_user.id)
    if row is None:
        return UserIncomeOut()
    return UserIncomeOut(statut=row.statut, revenu_net=row.revenu_net)
