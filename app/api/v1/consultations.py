"""API REST des Consultations (``/api/v1/consultations``).

Ordre des gardes : limite de débit -> 401 -> rôle -> validation (422) -> règle
métier. Le montage du routeur est fait ailleurs (tâche 16.1).

* lecture publique : liste et détail des consultations non brouillon (Exigence 9.10) ;
* participation : ``POST /{id}/responses`` (citoyen, limite de débit — 10.8, 10.13) ;
* résultats publics d'une consultation clôturée (11.2) ;
* administration (``content_admin``) : création, modification, publication, clôture ;
* export ``results.csv`` (``content_admin`` / ``super_admin``, ``text/csv``) ;
* modération des textes libres (``moderator`` / ``content_admin``) (11.11).

Correspondance des erreurs : introuvable -> 404, validation -> 422,
conflit / consultation non ouverte -> 409, rôle insuffisant pour l'export -> 403.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_roles
from app.core.database import get_session
from app.core.rate_limit import consultation_respond_rate_limit
from app.models.roles import Role
from app.schemas.auth import UserPublic
from app.schemas.consultation import (
    AnswerIn,
    ConsultationCreate,
    ConsultationPublic,
    ConsultationResults,
    ConsultationSummary,
)
from app.services.consultation_moderation_service import ConsultationModerationService
from app.services.consultation_results_service import (
    ConsultationResultsForbiddenError,
    ConsultationResultsService,
)
from app.services.consultation_service import (
    AnswerValidationError,
    ConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationService,
    ConsultationValidationError,
    effective_status,
    utcnow,
)

router = APIRouter()

_citizen_guard = require_roles(Role.CITIZEN)
_admin_guard = require_roles(Role.CONTENT_ADMIN)
_moderation_guard = require_roles(Role.MODERATOR, Role.CONTENT_ADMIN)

CitizenDep = Annotated[UserPublic, Depends(_citizen_guard)]
AdminDep = Annotated[UserPublic, Depends(_admin_guard)]
ModeratorDep = Annotated[UserPublic, Depends(_moderation_guard)]
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_consultation_service(session: SessionDep) -> ConsultationService:
    """Dépendance fournissant le service des consultations (surchargeable en test)."""
    return ConsultationService(session)


def get_consultation_results_service(session: SessionDep) -> ConsultationResultsService:
    """Dépendance fournissant le service des résultats (surchargeable en test)."""
    return ConsultationResultsService(session)


def get_consultation_moderation_service(session: SessionDep) -> ConsultationModerationService:
    """Dépendance fournissant le service de modération (surchargeable en test)."""
    return ConsultationModerationService(session)


ServiceDep = Annotated[ConsultationService, Depends(get_consultation_service)]
ResultsServiceDep = Annotated[ConsultationResultsService, Depends(get_consultation_results_service)]
ModerationServiceDep = Annotated[
    ConsultationModerationService, Depends(get_consultation_moderation_service)
]


class ConsultationAdminOut(BaseModel):
    """Consultation vue par un administrateur (statut stocké et statut effectif)."""

    id: int
    title: str
    stored_status: str
    status: str
    opens_at: datetime
    closes_at: datetime


class PendingTextOut(BaseModel):
    """Texte libre en attente de modération (aucun lien vers l'utilisateur)."""

    answer_id: int
    question_id: int
    prompt: str
    text: str


class TextReviewOut(BaseModel):
    """Résultat d'une modération de texte."""

    answer_id: int
    text_review: str


class AnswersBody(BaseModel):
    """Corps de ``POST /{id}/responses``."""

    answers: list[AnswerIn]


def _http(exc: Exception) -> HTTPException:
    """Traduit une erreur du domaine en ``HTTPException``."""
    if isinstance(exc, ConsultationNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, str(exc))
    if isinstance(exc, ConsultationResultsForbiddenError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, ConsultationConflictError):  # inclut ConsultationNotOpenError
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(exc, AnswerValidationError):
        return HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            {"message": str(exc), "question_id": exc.question_id},
        )
    if isinstance(exc, ConsultationValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))
    raise exc


def _admin_out(consultation) -> ConsultationAdminOut:  # type: ignore[no-untyped-def]
    return ConsultationAdminOut(
        id=consultation.id,
        title=consultation.title,
        stored_status=consultation.status,
        status=effective_status(consultation, utcnow()),
        opens_at=consultation.opens_at,
        closes_at=consultation.closes_at,
    )


# --------------------------------------------------------------------------- #
# Lecture publique
# --------------------------------------------------------------------------- #
@router.get(
    "",
    response_model=list[ConsultationSummary],
    summary="Lister les consultations publiées ou clôturées",
)
async def list_consultations(service: ServiceDep) -> list[ConsultationSummary]:
    return await service.list_public()


@router.get(
    "/{consultation_id}",
    response_model=ConsultationPublic,
    summary="Détail d'une consultation et de ses questions",
)
async def get_consultation(consultation_id: int, service: ServiceDep) -> ConsultationPublic:
    try:
        return await service.get_public(consultation_id)
    except ConsultationNotFoundError as exc:
        raise _http(exc) from exc


@router.get(
    "/{consultation_id}/results",
    response_model=ConsultationResults,
    summary="Résultats d'une consultation clôturée (participants volontaires)",
)
async def get_results(consultation_id: int, service: ResultsServiceDep) -> ConsultationResults:
    try:
        return await service.results(consultation_id)
    except ConsultationNotFoundError as exc:
        raise _http(exc) from exc


# --------------------------------------------------------------------------- #
# Participation (citoyen)
# --------------------------------------------------------------------------- #
@router.post(
    "/{consultation_id}/responses",
    status_code=status.HTTP_201_CREATED,
    summary="Répondre à une consultation ouverte (une fois par citoyen)",
    dependencies=[Depends(consultation_respond_rate_limit), Depends(_citizen_guard)],
)
async def respond(
    consultation_id: int,
    payload: AnswersBody,
    current_user: CitizenDep,
    service: ServiceDep,
) -> Response:
    try:
        await service.submit(current_user, consultation_id, payload.answers)
    except (
        ConsultationNotFoundError,
        ConsultationConflictError,
        ConsultationValidationError,
    ) as exc:
        raise _http(exc) from exc
    return Response(status_code=status.HTTP_201_CREATED)


# --------------------------------------------------------------------------- #
# Administration (content_admin)
# --------------------------------------------------------------------------- #
@router.post(
    "",
    response_model=ConsultationAdminOut,
    status_code=status.HTTP_201_CREATED,
    summary="Créer une consultation (brouillon)",
)
async def create_consultation(
    payload: ConsultationCreate, current_user: AdminDep, service: ServiceDep
) -> ConsultationAdminOut:
    try:
        return _admin_out(await service.create(current_user, payload))
    except (ConsultationConflictError, ConsultationValidationError) as exc:
        raise _http(exc) from exc


@router.put(
    "/{consultation_id}",
    response_model=ConsultationAdminOut,
    summary="Modifier une consultation sans réponses",
)
async def update_consultation(
    consultation_id: int,
    payload: ConsultationCreate,
    current_user: AdminDep,
    service: ServiceDep,
) -> ConsultationAdminOut:
    try:
        return _admin_out(await service.update(current_user, consultation_id, payload))
    except (
        ConsultationNotFoundError,
        ConsultationConflictError,
        ConsultationValidationError,
    ) as exc:
        raise _http(exc) from exc


@router.post(
    "/{consultation_id}/publish",
    response_model=ConsultationAdminOut,
    summary="Publier un brouillon",
)
async def publish_consultation(
    consultation_id: int, current_user: AdminDep, service: ServiceDep
) -> ConsultationAdminOut:
    try:
        return _admin_out(await service.publish(current_user, consultation_id))
    except (
        ConsultationNotFoundError,
        ConsultationConflictError,
        ConsultationValidationError,
    ) as exc:
        raise _http(exc) from exc


@router.post(
    "/{consultation_id}/close",
    response_model=ConsultationAdminOut,
    summary="Clôturer manuellement une consultation publiée",
)
async def close_consultation(
    consultation_id: int, current_user: AdminDep, service: ServiceDep
) -> ConsultationAdminOut:
    try:
        return _admin_out(await service.close(current_user, consultation_id))
    except (
        ConsultationNotFoundError,
        ConsultationConflictError,
        ConsultationValidationError,
    ) as exc:
        raise _http(exc) from exc


@router.get(
    "/{consultation_id}/results.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
    summary="Exporter les résultats en CSV (content_admin / super_admin)",
)
async def export_results_csv(
    consultation_id: int, current_user: AdminDep, service: ResultsServiceDep
) -> Response:
    try:
        body = await service.export_csv(current_user, consultation_id)
    except (
        ConsultationNotFoundError,
        ConsultationResultsForbiddenError,
        ConsultationConflictError,
        ConsultationValidationError,
    ) as exc:
        raise _http(exc) from exc
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="consultation-{consultation_id}.csv"'
        },
    )


# --------------------------------------------------------------------------- #
# Modération des textes libres (moderator / content_admin)
# --------------------------------------------------------------------------- #
@router.get(
    "/{consultation_id}/texts/pending",
    response_model=list[PendingTextOut],
    summary="Textes libres en attente de modération (consultation clôturée)",
)
async def pending_texts(
    consultation_id: int, current_user: ModeratorDep, service: ModerationServiceDep
) -> list[PendingTextOut]:
    try:
        rows = await service.pending(consultation_id)
    except ConsultationNotFoundError as exc:
        raise _http(exc) from exc
    return [
        PendingTextOut(
            answer_id=r.answer_id, question_id=r.question_id, prompt=r.prompt, text=r.text
        )
        for r in rows
    ]


@router.post(
    "/texts/{answer_id}/approve",
    response_model=TextReviewOut,
    summary="Approuver un texte libre",
)
async def approve_text(
    answer_id: int, current_user: ModeratorDep, service: ModerationServiceDep
) -> TextReviewOut:
    try:
        answer = await service.approve(current_user, answer_id)
    except (ConsultationNotFoundError, ConsultationConflictError) as exc:
        raise _http(exc) from exc
    return TextReviewOut(answer_id=answer.id, text_review=answer.text_review)


@router.post(
    "/texts/{answer_id}/reject",
    response_model=TextReviewOut,
    summary="Rejeter un texte libre (il reste compté, jamais publié)",
)
async def reject_text(
    answer_id: int, current_user: ModeratorDep, service: ModerationServiceDep
) -> TextReviewOut:
    try:
        answer = await service.reject(current_user, answer_id)
    except (ConsultationNotFoundError, ConsultationConflictError) as exc:
        raise _http(exc) from exc
    return TextReviewOut(answer_id=answer.id, text_review=answer.text_review)


__all__ = ["router"]
