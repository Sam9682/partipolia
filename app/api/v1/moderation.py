"""API de modération des Revendications (``/api/v1/moderation``) — Exigences 8.6 à 8.11.

Réservée aux rôles ``moderator`` et ``content_admin`` (``super_admin`` implicite) :
``401`` sans authentification, ``403`` sinon. Correspondance des erreurs :
:class:`ClaimNotFoundError` → ``404`` ; :class:`ClaimValidationError` → ``422`` ;
:class:`ClaimStateError` → ``409``. Le montage du routeur est réalisé ailleurs.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_session
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.claim import ModerationAction, ModerationItemOut
from app.services.claim_moderation_service import ClaimModerationService
from app.services.claim_service import (
    ClaimNotFoundError,
    ClaimStateError,
    ClaimValidationError,
)

router = APIRouter()

_moderator_guard = require_roles(Role.MODERATOR, Role.CONTENT_ADMIN)
ModeratorDep = Annotated[UserPublic, Depends(_moderator_guard)]
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
IdPath = Annotated[int, Path(ge=1)]


def get_claim_moderation_service(session: SessionDep) -> ClaimModerationService:
    """Dépendance fournissant le :class:`ClaimModerationService` (surchargeable en test)."""
    return ClaimModerationService(session)


ServiceDep = Annotated[ClaimModerationService, Depends(get_claim_moderation_service)]


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ClaimNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ClaimStateError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ClaimValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


_ERRORS = (ClaimNotFoundError, ClaimValidationError, ClaimStateError)


@router.get(
    "/queue",
    response_model=list[ModerationItemOut],
    summary="File de modération (contenus masqués en attente)",
    dependencies=[Depends(_moderator_guard)],
)
async def moderation_queue(service: ServiceDep) -> list[ModerationItemOut]:
    items = await service.queue()
    return [ModerationItemOut.model_validate(i, from_attributes=True) for i in items]


@router.post(
    "/claims/{claim_id}/hide",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Masquer une Revendication",
    dependencies=[Depends(_moderator_guard)],
)
async def hide_claim(
    claim_id: IdPath, payload: ModerationAction, moderator: ModeratorDep, service: ServiceDep
) -> None:
    try:
        await service.hide(moderator, claim_id, payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/claims/{claim_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rétablir une Revendication masquée",
    dependencies=[Depends(_moderator_guard)],
)
async def restore_claim(
    claim_id: IdPath, payload: ModerationAction, moderator: ModeratorDep, service: ServiceDep
) -> None:
    try:
        await service.restore(moderator, claim_id, payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/claims/{claim_id}/close",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clore une Revendication",
    dependencies=[Depends(_moderator_guard)],
)
async def close_claim(
    claim_id: IdPath, payload: ModerationAction, moderator: ModeratorDep, service: ServiceDep
) -> None:
    try:
        await service.close(moderator, claim_id, payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/comments/{comment_id}/hide",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Masquer un commentaire",
    dependencies=[Depends(_moderator_guard)],
)
async def hide_comment(
    comment_id: IdPath, payload: ModerationAction, moderator: ModeratorDep, service: ServiceDep
) -> None:
    try:
        await service.hide_comment(moderator, comment_id, payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/comments/{comment_id}/restore",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Rétablir un commentaire masqué",
    dependencies=[Depends(_moderator_guard)],
)
async def restore_comment(
    comment_id: IdPath, payload: ModerationAction, moderator: ModeratorDep, service: ServiceDep
) -> None:
    try:
        await service.restore_comment(moderator, comment_id, payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc
