"""Net contributor balance endpoint (anonymous access, rate limited).

The calculation itself is stateless and needs no account. When the caller is
logged in, the resulting verdict (verdict and balance only) is persisted after a
successful calculation; anonymous visitors never trigger any write, and invalid
input leaves any stored verdict untouched.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.middleware import error_response
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import UserPublic
from app.schemas.net_balance import NetBalanceRequest, NetBalanceResponse
from app.services.net_balance_service import NetBalanceService, NetBalanceValidationError
from app.services.net_balance_verdict_service import NetBalanceVerdictService
from app.services.simulation_service import DatabaseSnapshotProvider

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
    responses={status.HTTP_400_BAD_REQUEST: {"description": "Entrée invalide"}},
)
async def calculate(
    payload: NetBalanceRequest,
    service: Annotated[NetBalanceService, Depends(get_net_balance_service)],
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> NetBalanceResponse | JSONResponse:
    """Net balance; no account required, entered values are never stored or logged.

    The calculation is stateless. Only for a logged-in user, and only after a
    successful calculation, the verdict and balance are upserted (one per account).
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
    if current_user is not None:
        await NetBalanceVerdictService(session).record(
            current_user.id, response.verdict, response.bilan
        )
    return response
