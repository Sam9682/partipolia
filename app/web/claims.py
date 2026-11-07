"""Pages SSR des Revendications citoyennes (français) — Exigences 6, 7, 8.14 et 12.

Pages : ``/revendications`` (catalogue), ``/revendications/nouvelle`` (formulaire),
``/revendications/mes-revendications``, ``/revendications/{id}`` (détail) et
``/charte-de-moderation``.

Les actions (soutien, commentaire, signalement, publication, suppression) sont des
formulaires ``<form method="post">`` qui fonctionnent sans JavaScript (redirection
``303``). Pour le soutien, HTMX remplace seulement le bloc compteur/bouton ; en cas
d'échec, un message en français est affiché dans une zone dédiée et l'état affiché
n'est pas modifié. Aucun texte utilisateur n'est rendu avec ``|safe`` : l'échappement
automatique de Jinja2 s'applique partout.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import claim_create_rate_limit, claim_support_rate_limit
from app.models.roles import Role, has_role
from app.schemas.auth import UserPublic
from app.schemas.claim import REPORT_REASON_LABELS, ClaimCreate, ReportCreate
from app.services.aggregation_service import MIN_COHORT_SIZE, AggregationService
from app.services.claim_service import (
    CATALOGUE_SORTS,
    ClaimConflictError,
    ClaimNotFoundError,
    ClaimService,
    ClaimStateError,
    ClaimValidationError,
    ReportTarget,
)
from app.web.national import _CATEGORIES, CATEGORY_NAMES  # noqa: F401  (enregistre les filtres)
from app.web.public import page_context, templates

claims_web_router: APIRouter = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
OptionalUser = Annotated[UserPublic | None, Depends(get_current_user_optional)]

SORT_LABELS: dict[str, str] = {
    "recent": "Les plus récentes",
    "supports": "Les plus soutenues",
    "category": "Par catégorie",
}
STATUS_LABELS: dict[str, str] = {
    "draft": "Brouillon",
    "published": "Publiée",
    "hidden": "Masquée",
    "closed": "Clôturée",
}
# Messages de confirmation : clés fixes (jamais de texte reflété depuis l'URL).
INFO_MESSAGES: dict[str, str] = {
    "brouillon": "Votre brouillon est enregistré. Il n'est visible que de vous.",
    "publie": "Votre revendication est publiée.",
    "signale": "Merci, votre signalement est enregistré.",
    "commente": "Votre commentaire est publié.",
    "supprime": "La revendication est supprimée.",
}
_MESSAGE_ERRORS = (ClaimNotFoundError, ClaimValidationError, ClaimStateError, ClaimConflictError)
_FIELD_ERRORS: dict[str, str] = {
    "title": "Le titre doit comporter de 5 à 120 caractères.",
    "body": "Le texte doit comporter de 20 à 2000 caractères.",
    "category_code": "Catégorie de consommation invalide.",
}


# --------------------------------------------------------------------------- #
# Aides
# --------------------------------------------------------------------------- #
def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _is_citizen(user: UserPublic | None) -> bool:
    return user is not None and has_role(user.role, Role.CITIZEN)


def _status_for(exc: Exception) -> int:
    if isinstance(exc, ClaimNotFoundError):
        return 404
    if isinstance(exc, ClaimStateError):  # inclut ClaimConflictError
        return 409
    return 422


def _access_error(user: UserPublic | None) -> tuple[int, str]:
    if user is None:
        return 401, "Connectez-vous avec un compte citoyen pour effectuer cette action."
    return 403, "Cette action est réservée aux comptes citoyens."


def _message_page(
    request: Request, message: str, code: int, back_href: str = "/revendications"
) -> HTMLResponse:
    """Page complète de message (repli sans JavaScript)."""
    return templates.TemplateResponse(
        request,
        "public/claim_message.html",
        page_context(request, message=message, back_href=back_href),
        status_code=code,
    )


def _fragment_error(
    request: Request, message: str, code: int, target_id: str, form_id: str
) -> HTMLResponse:
    """Fragment d'erreur HTMX : remplace seulement la zone d'erreur, pas l'état affiché."""
    return templates.TemplateResponse(
        request,
        "public/partials/claim_error.html",
        {"message": message, "target_id": target_id, "form_id": form_id},
        status_code=code,
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


async def _category_impact(session: AsyncSession, code: str | None) -> dict[str, Any] | None:
    """Impact de la catégorie du dernier agrégat national non retenu (Exigence 6.3)."""
    if not code:
        return None
    row = await AggregationService(session).latest()
    if row is None or row.participant_count < MIN_COHORT_SIZE:
        return None
    entry = ((row.details or {}).get("by_category") or {}).get(code)
    if not entry:
        return None
    try:
        participants = int(entry.get("participants", 0))
        avoided = Decimal(str(entry["avoided_expenditure"]))
        net = Decimal(str(entry["net_public_impact"]))
    except (KeyError, ValueError, InvalidOperation):
        return None
    if participants < MIN_COHORT_SIZE:
        return None
    return {
        "label": CATEGORY_NAMES.get(code, "Autres catégories"),
        "participants": participants,
        "avoided": avoided,
        "net": net,
        "snapshot_date": row.snapshot_date,
    }


def _parse_page(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value >= 1 else None


# --------------------------------------------------------------------------- #
# Charte de modération
# --------------------------------------------------------------------------- #
@claims_web_router.get(
    "/charte-de-moderation",
    response_class=HTMLResponse,
    name="charte_moderation",
    include_in_schema=False,
)
async def moderation_charter(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/moderation_charter.html",
        page_context(request, reasons=REPORT_REASON_LABELS),
    )


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #
@claims_web_router.get(
    "/revendications",
    response_class=HTMLResponse,
    name="claims_catalogue",
    include_in_schema=False,
)
async def claims_catalogue(
    request: Request,
    session: SessionDep,
    viewer: OptionalUser,
    sort: str = "recent",
    page: str = "1",
) -> HTMLResponse:
    number = _parse_page(page)
    if number is None:
        return _message_page(request, "Numéro de page invalide.", 422)
    if sort not in CATALOGUE_SORTS:
        return _message_page(request, "Tri inconnu.", 422)
    result = await ClaimService(session).catalogue(viewer.id if viewer else None, sort, number)
    return templates.TemplateResponse(
        request,
        "public/claims.html",
        page_context(
            request,
            result=result,
            sort=sort,
            sort_labels=SORT_LABELS,
            authenticated=_is_citizen(viewer),
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
        ),
    )


# --------------------------------------------------------------------------- #
# Création
# --------------------------------------------------------------------------- #
def _new_form_response(
    request: Request,
    viewer: UserPublic | None,
    values: dict[str, str],
    errors: dict[str, str],
    banner: str | None = None,
    code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/claim_new.html",
        page_context(
            request,
            categories=_CATEGORIES,
            values=values,
            errors=errors,
            banner=banner,
            authenticated=_is_citizen(viewer),
        ),
        status_code=code,
    )


@claims_web_router.get(
    "/revendications/nouvelle",
    response_class=HTMLResponse,
    name="claim_new",
    include_in_schema=False,
)
async def claim_new_page(request: Request, viewer: OptionalUser) -> HTMLResponse:
    return _new_form_response(request, viewer, {}, {})


@claims_web_router.post(
    "/revendications/nouvelle",
    response_class=HTMLResponse,
    name="claim_new_submit",
    include_in_schema=False,
    dependencies=[Depends(claim_create_rate_limit)],
)
async def claim_new_submit(request: Request, session: SessionDep, viewer: OptionalUser) -> Response:
    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return _message_page(request, message, code, "/login")
    assert viewer is not None
    form = await request.form()
    values = {
        "title": str(form.get("title", "")),
        "body": str(form.get("body", "")),
        "category_code": str(form.get("category_code", "")),
    }
    try:
        payload = ClaimCreate.model_validate(
            {
                "title": values["title"],
                "body": values["body"],
                "category_code": values["category_code"] or None,
            }
        )
    except ValidationError as exc:
        errors = {
            str(e["loc"][0]): _FIELD_ERRORS.get(str(e["loc"][0]), "Valeur invalide.")
            for e in exc.errors()
            if e.get("loc")
        }
        return _new_form_response(request, viewer, values, errors, code=422)

    service = ClaimService(session)
    try:
        claim = await service.create_draft(viewer.id, payload)
    except ClaimValidationError as exc:
        return _new_form_response(request, viewer, values, {}, banner=str(exc), code=422)
    if str(form.get("action", "")) != "publier":
        return _redirect("/revendications/mes-revendications?info=brouillon")
    try:
        await service.publish(viewer.id, claim.id)
    except _MESSAGE_ERRORS as exc:
        # Le brouillon reste enregistré (Exigence 6.9).
        return _message_page(
            request,
            f"{exc} Votre brouillon est conservé.",
            _status_for(exc),
            "/revendications/mes-revendications",
        )
    return _redirect(f"/revendications/{claim.id}?info=publie")


# --------------------------------------------------------------------------- #
# Mes revendications
# --------------------------------------------------------------------------- #
@claims_web_router.get(
    "/revendications/mes-revendications",
    response_class=HTMLResponse,
    name="claims_mine",
    include_in_schema=False,
)
async def claims_mine(request: Request, session: SessionDep, viewer: OptionalUser) -> HTMLResponse:
    authenticated = _is_citizen(viewer)
    claims: list[Any] = []
    if authenticated:
        assert viewer is not None
        claims = await ClaimService(session).mine(viewer.id)
    return templates.TemplateResponse(
        request,
        "public/claims_mine.html",
        page_context(
            request,
            claims=claims,
            authenticated=authenticated,
            status_labels=STATUS_LABELS,
            category_names=CATEGORY_NAMES,
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
        ),
    )


@claims_web_router.post(
    "/revendications/{claim_id:int}/publier",
    response_class=HTMLResponse,
    name="claim_publish",
    include_in_schema=False,
    dependencies=[Depends(claim_create_rate_limit)],
)
async def claim_publish(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return _message_page(request, message, code, "/login")
    assert viewer is not None
    try:
        await ClaimService(session).publish(viewer.id, claim_id)
    except _MESSAGE_ERRORS as exc:
        return _message_page(
            request, str(exc), _status_for(exc), "/revendications/mes-revendications"
        )
    return _redirect(f"/revendications/{claim_id}?info=publie")


@claims_web_router.post(
    "/revendications/{claim_id:int}/supprimer",
    response_class=HTMLResponse,
    name="claim_delete",
    include_in_schema=False,
)
async def claim_delete(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return _message_page(request, message, code, "/login")
    assert viewer is not None
    try:
        await ClaimService(session).delete(viewer.id, claim_id)
    except _MESSAGE_ERRORS as exc:
        return _message_page(
            request, str(exc), _status_for(exc), "/revendications/mes-revendications"
        )
    return _redirect("/revendications/mes-revendications?info=supprime")


# --------------------------------------------------------------------------- #
# Détail
# --------------------------------------------------------------------------- #
@claims_web_router.get(
    "/revendications/{claim_id:int}",
    response_class=HTMLResponse,
    name="claim_detail",
    include_in_schema=False,
)
async def claim_detail(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> HTMLResponse:
    service = ClaimService(session)
    can_moderate = viewer is not None and has_role(viewer.role, Role.MODERATOR, Role.CONTENT_ADMIN)
    try:
        claim = await service.get_detail(
            viewer.id if viewer else None, claim_id, can_moderate=can_moderate
        )
    except ClaimNotFoundError:
        return _message_page(request, "Revendication introuvable.", 404)
    comments = await service.list_comments(claim_id) if claim.status == "published" else []
    impact = await _category_impact(session, claim.category_code)
    return templates.TemplateResponse(
        request,
        "public/claim_detail.html",
        page_context(
            request,
            claim=claim,
            comments=comments,
            impact=impact,
            min_cohort=MIN_COHORT_SIZE,
            reasons=REPORT_REASON_LABELS,
            status_labels=STATUS_LABELS,
            authenticated=_is_citizen(viewer),
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
        ),
    )


# --------------------------------------------------------------------------- #
# Soutien (HTMX + repli formulaire)
# --------------------------------------------------------------------------- #
@claims_web_router.post(
    "/revendications/{claim_id:int}/soutien",
    response_class=HTMLResponse,
    name="claim_support",
    include_in_schema=False,
    dependencies=[Depends(claim_support_rate_limit)],
)
async def claim_support(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    target_id, form_id = f"support-error-{claim_id}", f"support-form-{claim_id}"
    back = f"/revendications/{claim_id}"

    def fail(message: str, code: int) -> Response:
        if _is_htmx(request):
            return _fragment_error(request, message, code, target_id, form_id)
        return _message_page(request, message, code, back)

    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return fail(message, code)
    assert viewer is not None
    form = await request.form()
    service = ClaimService(session)
    try:
        if str(form.get("operation", "")) == "retirer":
            state = await service.unsupport(viewer.id, claim_id)
        else:
            state = await service.support(viewer.id, claim_id)
    except _MESSAGE_ERRORS as exc:
        return fail(str(exc), _status_for(exc))
    if _is_htmx(request):
        return templates.TemplateResponse(
            request,
            "public/partials/claim_support.html",
            {
                "claim_id": claim_id,
                "supported": state.supported,
                "count": state.support_count,
                "authenticated": True,
            },
        )
    return _redirect(back)


# --------------------------------------------------------------------------- #
# Commentaires et signalements (formulaires classiques, redirection 303)
# --------------------------------------------------------------------------- #
@claims_web_router.post(
    "/revendications/{claim_id:int}/commentaires",
    response_class=HTMLResponse,
    name="claim_comment",
    include_in_schema=False,
    dependencies=[Depends(claim_create_rate_limit)],
)
async def claim_comment(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    back = f"/revendications/{claim_id}"
    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return _message_page(request, message, code, back)
    assert viewer is not None
    form = await request.form()
    try:
        await ClaimService(session).comment(viewer.id, claim_id, str(form.get("body", "")))
    except _MESSAGE_ERRORS as exc:
        return _message_page(request, str(exc), _status_for(exc), back)
    return _redirect(f"{back}?info=commente")


async def _report(
    request: Request,
    session: AsyncSession,
    viewer: UserPublic | None,
    claim_id: int,
    comment_id: int | None,
) -> Response:
    back = f"/revendications/{claim_id}"
    if not _is_citizen(viewer):
        code, message = _access_error(viewer)
        return _message_page(request, message, code, back)
    assert viewer is not None
    form = await request.form()
    try:
        reason = ReportCreate.model_validate({"reason": str(form.get("reason", ""))}).reason
    except ValidationError:
        return _message_page(request, "Motif de signalement invalide.", 422, back)
    service = ClaimService(session)
    try:
        if comment_id is None:
            await service.report(viewer.id, ReportTarget("claim", claim_id), reason)
        else:
            await service.ensure_comment_in_claim(claim_id, comment_id)
            await service.report(viewer.id, ReportTarget("comment", comment_id), reason)
    except _MESSAGE_ERRORS as exc:
        return _message_page(request, str(exc), _status_for(exc), back)
    return _redirect(f"{back}?info=signale")


@claims_web_router.post(
    "/revendications/{claim_id:int}/signaler",
    response_class=HTMLResponse,
    name="claim_report",
    include_in_schema=False,
)
async def claim_report(
    request: Request, claim_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    return await _report(request, session, viewer, claim_id, None)


@claims_web_router.post(
    "/revendications/{claim_id:int}/commentaires/{comment_id:int}/signaler",
    response_class=HTMLResponse,
    name="claim_comment_report",
    include_in_schema=False,
)
async def claim_comment_report(
    request: Request, claim_id: int, comment_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    return await _report(request, session, viewer, claim_id, comment_id)
