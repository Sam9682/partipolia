"""Citizen commitments and profile endpoints (authenticated)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit
from app.models.simulation import EconomicSimulation
from app.schemas.auth import UserPublic
from app.schemas.commitment import (
    CitizenExport,
    CommitmentCreate,
    CommitmentOut,
    CommitmentUpdate,
    MyCommitments,
    ProfileOut,
    ProfileUpdate,
)
from app.schemas.simulation import SimulationRequest
from app.services.commitment_service import (
    CONSENT_KEY,
    CommitmentConflictError,
    CommitmentError,
    CommitmentInvalidError,
    CommitmentNotFoundError,
    CommitmentService,
    consent_of,
    get_profile,
    reductions_of,
)
from app.services.simulation_service import DatabaseSnapshotProvider, SimulationService

router = APIRouter()
profile_router = APIRouter()

CurrentUser = Annotated[UserPublic, Depends(get_current_user)]
Session = Annotated[AsyncSession, Depends(get_session, scope="function")]
Limit = Depends(default_rate_limit("commitments"))


def _http(exc: CommitmentError) -> HTTPException:
    if isinstance(exc, CommitmentNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "Engagement introuvable.")
    if isinstance(exc, CommitmentConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, CommitmentInvalidError):
        return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))


@router.post(
    "",
    response_model=CommitmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Limit],
    summary="Créer un engagement (déclaratif)",
)
async def create_commitment(
    payload: CommitmentCreate, session: Session, user: CurrentUser
) -> CommitmentOut:
    try:
        return await CommitmentService(session).create(user.id, payload)
    except CommitmentError as exc:
        raise _http(exc) from exc


@router.get("/me", response_model=MyCommitments, summary="Mes engagements et leur impact estimé")
async def my_commitments(session: Session, user: CurrentUser) -> MyCommitments:
    commitments = await CommitmentService(session).list_for(user.id)
    account = await get_profile(session, user.id)
    reductions = reductions_of(commitments)
    simulation = None
    if reductions:
        service = SimulationService(DatabaseSnapshotProvider(session), session)
        simulation = await service.calculate(
            SimulationRequest(reductions=reductions, adults=account.household_adults), user.id
        )
    return MyCommitments(
        commitments=commitments,
        consent_aggregation=consent_of(account),
        simulation=simulation,
    )


@router.put(
    "/{commitment_id}",
    response_model=CommitmentOut,
    dependencies=[Limit],
    summary="Modifier, suspendre, réactiver ou renouveler un engagement",
)
async def update_commitment(
    commitment_id: int, payload: CommitmentUpdate, session: Session, user: CurrentUser
) -> CommitmentOut:
    try:
        return await CommitmentService(session).update(user.id, commitment_id, payload)
    except CommitmentError as exc:
        raise _http(exc) from exc


@router.delete(
    "/{commitment_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Limit],
    summary="Supprimer un engagement",
)
async def delete_commitment(commitment_id: int, session: Session, user: CurrentUser) -> Response:
    try:
        await CommitmentService(session).delete(user.id, commitment_id)
    except CommitmentError as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- profile
def _profile(account) -> ProfileOut:  # type: ignore[no-untyped-def]
    return ProfileOut(
        country=account.country,
        region=account.region,
        household_adults=account.household_adults,
        consent_aggregation=consent_of(account),
    )


@profile_router.get("/me", response_model=ProfileOut, summary="Mon profil (données minimales)")
async def read_profile(session: Session, user: CurrentUser) -> ProfileOut:
    return _profile(await get_profile(session, user.id))


@profile_router.get(
    "/me/export",
    response_model=CitizenExport,
    summary="Exporter mes données personnelles (RGPD)",
)
async def export_my_data(session: Session, user: CurrentUser) -> CitizenExport:
    account = await get_profile(session, user.id)
    saved = (
        await session.execute(
            select(EconomicSimulation)
            .where(EconomicSimulation.user_id == user.id)
            .order_by(EconomicSimulation.id)
        )
    ).scalars()
    return CitizenExport(
        profile=_profile(account),
        email=account.email,
        pseudonym=account.display_name,
        commitments=await CommitmentService(session).list_for(user.id),
        saved_simulations=[
            {
                "id": s.id,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "model_version": s.model_version,
                "input": s.input_payload,
            }
            for s in saved
        ],
    )


@profile_router.put(
    "/me",
    response_model=ProfileOut,
    dependencies=[Limit],
    summary="Modifier mon profil et mon consentement à l'agrégation",
)
async def update_profile(payload: ProfileUpdate, session: Session, user: CurrentUser) -> ProfileOut:
    account = await get_profile(session, user.id)
    if payload.country is not None:
        account.country = payload.country
    if payload.region is not None:
        account.region = payload.region
    if payload.household_adults is not None:
        account.household_adults = payload.household_adults
    if payload.consent_aggregation is not None:
        # Reassign the dict so SQLAlchemy detects the JSONB change.
        account.consents = {**(account.consents or {}), CONSENT_KEY: payload.consent_aggregation}
    await session.flush()
    return _profile(account)
