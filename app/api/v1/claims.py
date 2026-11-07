"""API REST des Revendications citoyennes (``/api/v1/claims``) — Exigences 6, 7 et 8.

Ordre des gardes sur chaque route : limite de débit → ``401`` → rôle (``403``) →
propriété (``404``) → validation (``422``) → règle métier. Correspondance des
erreurs du Service :

* :class:`ClaimNotFoundError` → ``404`` (absente, non publiée ou appartenant à autrui) ;
* :class:`ClaimValidationError` (et sous-classes) → ``422`` ;
* :class:`ClaimStateError` / :class:`ClaimConflictError` → ``409``.

Le montage du routeur est réalisé ailleurs (``router.py``).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional, require_roles
from app.core.database import get_session
from app.core.rate_limit import claim_create_rate_limit, claim_support_rate_limit
from app.models.roles import Role, has_role
from app.schemas.auth import UserPublic
from app.schemas.claim import (
    ClaimCreate,
    ClaimDetail,
    ClaimOwnerView,
    ClaimPage,
    CommentCreate,
    CommentItem,
    ReportCreate,
    SupportState,
)
from app.services.claim_service import (
    ClaimConflictError,
    ClaimNotFoundError,
    ClaimService,
    ClaimStateError,
    ClaimValidationError,
    ReportTarget,
)

router = APIRouter()

_citizen_guard = require_roles(Role.CITIZEN)
CitizenDep = Annotated[UserPublic, Depends(_citizen_guard)]
OptionalUserDep = Annotated[UserPublic | None, Depends(get_current_user_optional)]
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
ClaimIdPath = Annotated[int, Path(ge=1, description="Identifiant de la Revendication")]
CommentIdPath = Annotated[int, Path(ge=1, description="Identifiant du commentaire")]

_NOT_FOUND = "Revendication introuvable."


def get_claim_service(session: SessionDep) -> ClaimService:
    """Dépendance fournissant le :class:`ClaimService` (surchargeable en test)."""
    return ClaimService(session)


ServiceDep = Annotated[ClaimService, Depends(get_claim_service)]


def _http_error(exc: Exception) -> HTTPException:
    """Traduit une erreur du Service en ``HTTPException`` (404 / 409 / 422)."""
    if isinstance(exc, ClaimNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc) or _NOT_FOUND)
    if isinstance(exc, ClaimStateError):  # inclut ClaimConflictError
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, ClaimValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


_ERRORS = (ClaimNotFoundError, ClaimValidationError, ClaimStateError, ClaimConflictError)


@router.get(
    "",
    response_model=ClaimPage,
    summary="Catalogue des Revendications publiées",
)
async def list_claims(
    service: ServiceDep,
    viewer: OptionalUserDep,
    sort: Annotated[str, Query(description="recent (défaut), supports ou category")] = "recent",
    page: Annotated[int, Query(description="Numéro de page (≥ 1)")] = 1,
) -> ClaimPage:
    try:
        return await service.catalogue(viewer.id if viewer else None, sort, page)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/mine",
    response_model=list[ClaimOwnerView],
    summary="Mes Revendications (motif de masquage visible)",
)
async def my_claims(current_user: CitizenDep, service: ServiceDep) -> list[ClaimOwnerView]:
    return await service.mine(current_user.id)


@router.post(
    "",
    response_model=ClaimDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Créer un brouillon de Revendication",
    dependencies=[Depends(claim_create_rate_limit), Depends(_citizen_guard)],
)
async def create_claim(
    payload: ClaimCreate, current_user: CitizenDep, service: ServiceDep
) -> ClaimDetail:
    try:
        claim = await service.create_draft(current_user.id, payload)
        return await service.get_detail(current_user.id, claim.id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/{claim_id}",
    response_model=ClaimDetail,
    summary="Détail d'une Revendication",
)
async def get_claim(
    claim_id: ClaimIdPath, service: ServiceDep, viewer: OptionalUserDep
) -> ClaimDetail:
    can_moderate = viewer is not None and has_role(viewer.role, Role.MODERATOR, Role.CONTENT_ADMIN)
    try:
        return await service.get_detail(
            viewer.id if viewer else None, claim_id, can_moderate=can_moderate
        )
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/{claim_id}",
    response_model=ClaimDetail,
    summary="Modifier une Revendication (brouillon ou < 15 min après publication)",
    dependencies=[Depends(_citizen_guard)],
)
async def update_claim(
    claim_id: ClaimIdPath,
    payload: ClaimCreate,
    current_user: CitizenDep,
    service: ServiceDep,
) -> ClaimDetail:
    try:
        await service.update(current_user.id, claim_id, payload)
        return await service.get_detail(current_user.id, claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{claim_id}/publish",
    response_model=ClaimDetail,
    summary="Publier un brouillon",
    dependencies=[Depends(_citizen_guard)],
)
async def publish_claim(
    claim_id: ClaimIdPath, current_user: CitizenDep, service: ServiceDep
) -> ClaimDetail:
    try:
        await service.publish(current_user.id, claim_id)
        return await service.get_detail(current_user.id, claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/{claim_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Supprimer une Revendication (cascade soutiens et commentaires)",
    dependencies=[Depends(_citizen_guard)],
)
async def delete_claim(
    claim_id: ClaimIdPath, current_user: CitizenDep, service: ServiceDep
) -> None:
    try:
        await service.delete(current_user.id, claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.put(
    "/{claim_id}/support",
    response_model=SupportState,
    summary="Soutenir une Revendication (idempotent)",
    dependencies=[Depends(claim_support_rate_limit), Depends(_citizen_guard)],
)
async def support_claim(
    claim_id: ClaimIdPath, current_user: CitizenDep, service: ServiceDep
) -> SupportState:
    try:
        return await service.support(current_user.id, claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.delete(
    "/{claim_id}/support",
    response_model=SupportState,
    summary="Retirer son soutien (idempotent)",
    dependencies=[Depends(claim_support_rate_limit), Depends(_citizen_guard)],
)
async def unsupport_claim(
    claim_id: ClaimIdPath, current_user: CitizenDep, service: ServiceDep
) -> SupportState:
    try:
        return await service.unsupport(current_user.id, claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.get(
    "/{claim_id}/comments",
    response_model=list[CommentItem],
    summary="Commentaires visibles d'une Revendication",
)
async def list_comments(claim_id: ClaimIdPath, service: ServiceDep) -> list[CommentItem]:
    try:
        return await service.list_comments(claim_id)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{claim_id}/comments",
    response_model=CommentItem,
    status_code=status.HTTP_201_CREATED,
    summary="Commenter une Revendication (1..1000 caractères)",
    dependencies=[Depends(_citizen_guard)],
)
async def create_comment(
    claim_id: ClaimIdPath,
    payload: CommentCreate,
    current_user: CitizenDep,
    service: ServiceDep,
) -> CommentItem:
    try:
        comment = await service.comment(current_user.id, claim_id, payload.body)
    except _ERRORS as exc:
        raise _http_error(exc) from exc
    return CommentItem(
        id=comment.id,
        claim_id=comment.claim_id,
        body=comment.body,
        author_pseudonym=current_user.display_name,
        created_at=comment.created_at,
    )


@router.post(
    "/{claim_id}/reports",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Signaler une Revendication",
    dependencies=[Depends(_citizen_guard)],
)
async def report_claim(
    claim_id: ClaimIdPath,
    payload: ReportCreate,
    current_user: CitizenDep,
    service: ServiceDep,
) -> None:
    try:
        await service.report(current_user.id, ReportTarget("claim", claim_id), payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc


@router.post(
    "/{claim_id}/comments/{comment_id}/reports",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Signaler un commentaire",
    dependencies=[Depends(_citizen_guard)],
)
async def report_comment(
    claim_id: ClaimIdPath,
    comment_id: CommentIdPath,
    payload: ReportCreate,
    current_user: CitizenDep,
    service: ServiceDep,
) -> None:
    try:
        await service.ensure_comment_in_claim(claim_id, comment_id)
        await service.report(current_user.id, ReportTarget("comment", comment_id), payload.reason)
    except _ERRORS as exc:
        raise _http_error(exc) from exc
