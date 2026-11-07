"""Pages SSR des Consultations (français) — Exigences 11.9 et 12.

Pages publiques : ``/consultations`` (liste), ``/consultations/{id}`` (détail et
formulaire de réponse), ``/consultations/{id}/resultats`` (résultats d'une
consultation clôturée, toujours accompagnés de la mention « participants
volontaires, résultats non représentatifs »).

Pages d'administration (``content_admin`` ; consultation et modération des textes
aussi pour ``moderator``) : ``/admin/consultations`` et sous-pages.

La réponse se fait par un formulaire ``<form method="post">`` qui fonctionne sans
JavaScript. Avec HTMX, seule la zone d'erreur est remplacée : le formulaire n'est
jamais rendu à nouveau, la saisie est donc conservée. Sans JavaScript, le formulaire
est rendu à nouveau avec la saisie. Aucun texte utilisateur n'est rendu avec
``|safe`` : l'échappement automatique de Jinja2 s'applique partout.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import FormData

from app.api.deps import _audit_admin_denied, get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import consultation_respond_rate_limit
from app.models.consultation import Consultation, ConsultationQuestion
from app.models.roles import Role, has_role
from app.schemas.auth import UserPublic
from app.schemas.consultation import (
    MAX_QUESTIONS,
    REPRESENTATIVITY_NOTICE,
    AnswerIn,
    ConsultationCreate,
    ConsultationPublic,
    ConsultationResults,
)
from app.services.consultation_moderation_service import ConsultationModerationService
from app.services.consultation_results_service import ConsultationResultsService
from app.services.consultation_service import (
    AnswerValidationError,
    ConsultationConflictError,
    ConsultationNotFoundError,
    ConsultationNotOpenError,
    ConsultationService,
    ConsultationValidationError,
    effective_status,
    utcnow,
)
from app.web.formatting import format_number_fr
from app.web.national import _bars  # noqa: F401  (enregistre aussi les filtres Jinja)
from app.web.public import page_context, templates

consultations_web_router: APIRouter = APIRouter()

SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
OptionalUser = Annotated[UserPublic | None, Depends(get_current_user_optional)]

STATUS_LABELS: dict[str, str] = {
    "draft": "Brouillon",
    "scheduled": "Programmée",
    "open": "Ouverte",
    "closed": "Clôturée",
}
TYPE_LABELS: dict[str, str] = {
    "single_choice": "Choix unique",
    "multiple_choice": "Choix multiple",
    "scale": "Échelle de 1 à 5",
    "text": "Texte libre",
}
# Messages de confirmation : clés fixes (jamais de texte reflété depuis l'URL).
INFO_MESSAGES: dict[str, str] = {
    "repondu": "Merci, votre réponse est enregistrée.",
    "creee": "La consultation est créée en brouillon.",
    "modifiee": "La consultation est modifiée.",
    "publiee": "La consultation est publiée.",
    "cloturee": "La consultation est clôturée.",
    "texte-approuve": "Le texte est approuvé.",
    "texte-rejete": "Le texte est rejeté.",
}
DEFAULT_QUESTION_SLOTS = 3
CHOICE_TYPES = ("single_choice", "multiple_choice")
_QUESTION_TYPES = tuple(TYPE_LABELS)
_FIELD_LABELS: dict[str, str] = {
    "title": "Titre (5 à 120 caractères)",
    "description": "Description (20 à 5000 caractères)",
    "opens_at": "Date d'ouverture",
    "closes_at": "Date de clôture",
    "prompt": "Intitulé (5 à 300 caractères)",
    "options": "Options (2 à 10, de 1 à 200 caractères, distinctes)",
    "question_type": "Type de question",
}


def _datetime_fr(value: datetime | None) -> str:
    return "—" if value is None else value.strftime("%d/%m/%Y %H:%M")


templates.env.filters["datetime_fr"] = _datetime_fr


# --------------------------------------------------------------------------- #
# Aides communes
# --------------------------------------------------------------------------- #
def _is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


def _is_citizen(user: UserPublic | None) -> bool:
    return user is not None and has_role(user.role, Role.CITIZEN)


def _message_page(
    request: Request, message: str, code: int, back_href: str = "/consultations"
) -> HTMLResponse:
    """Page complète de message (repli sans JavaScript)."""
    return templates.TemplateResponse(
        request,
        "public/consultation_message.html",
        page_context(request, message=message, back_href=back_href),
        status_code=code,
    )


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _error_fragment(request: Request, message: str, code: int, form_id: str) -> HTMLResponse:
    """Fragment d'erreur HTMX : la saisie du formulaire est conservée."""
    return templates.TemplateResponse(
        request,
        "public/partials/national_error.html",
        {"message": message, "retry_target": form_id},
        status_code=code,
    )


def _http_status(exc: Exception) -> int:
    if isinstance(exc, ConsultationNotFoundError):
        return 404
    if isinstance(exc, ConsultationConflictError):  # inclut ConsultationNotOpenError
        return 409
    return 422


_DOMAIN_ERRORS = (
    ConsultationNotFoundError,
    ConsultationConflictError,
    ConsultationValidationError,
)


# --------------------------------------------------------------------------- #
# Liste publique
# --------------------------------------------------------------------------- #
@consultations_web_router.get(
    "/consultations",
    response_class=HTMLResponse,
    name="consultations",
    include_in_schema=False,
)
async def consultations_list(request: Request, session: SessionDep) -> HTMLResponse:
    items = await ConsultationService(session).list_public()
    return templates.TemplateResponse(
        request,
        "public/consultations.html",
        page_context(request, items=items, status_labels=STATUS_LABELS),
    )


# --------------------------------------------------------------------------- #
# Détail et réponse
# --------------------------------------------------------------------------- #
def _detail_response(
    request: Request,
    consultation: ConsultationPublic,
    viewer: UserPublic | None,
    values: dict[int, Any] | None = None,
    errors: dict[int, str] | None = None,
    banner: str | None = None,
    code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "public/consultation_detail.html",
        page_context(
            request,
            consultation=consultation,
            status_labels=STATUS_LABELS,
            type_labels=TYPE_LABELS,
            authenticated=_is_citizen(viewer),
            values=values or {},
            errors=errors or {},
            banner=banner,
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
            notice=REPRESENTATIVITY_NOTICE,
        ),
        status_code=code,
    )


@consultations_web_router.get(
    "/consultations/{consultation_id:int}",
    response_class=HTMLResponse,
    name="consultation_detail",
    include_in_schema=False,
)
async def consultation_detail(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> HTMLResponse:
    try:
        consultation = await ConsultationService(session).get_public(consultation_id)
    except ConsultationNotFoundError:
        return _message_page(request, "Consultation introuvable.", 404)
    return _detail_response(request, consultation, viewer)


def _read_answers(
    consultation: ConsultationPublic, form: FormData
) -> tuple[list[AnswerIn], dict[int, Any], dict[int, str]]:
    """Construit les réponses depuis le formulaire ; renvoie aussi la saisie et les erreurs."""
    answers: list[AnswerIn] = []
    values: dict[int, Any] = {}
    errors: dict[int, str] = {}
    for q in consultation.questions:
        key = f"q_{q.id}"
        try:
            if q.question_type == "multiple_choice":
                selected = [str(v) for v in form.getlist(key)]
                values[q.id] = selected
                if selected:
                    answers.append(AnswerIn(question_id=q.id, selected_options=selected))
            elif q.question_type == "single_choice":
                chosen = str(form.get(key, "")).strip()
                values[q.id] = chosen
                if chosen:
                    answers.append(AnswerIn(question_id=q.id, selected_options=[chosen]))
            elif q.question_type == "scale":
                raw = str(form.get(key, "")).strip()
                values[q.id] = raw
                if raw:
                    if not raw.isdigit():
                        raise ValueError("l'échelle doit être un entier de 1 à 5.")
                    answers.append(AnswerIn(question_id=q.id, scale_value=int(raw)))
            else:
                text = str(form.get(key, ""))
                values[q.id] = text
                if text.strip():
                    answers.append(AnswerIn(question_id=q.id, text_value=text))
        except (ValidationError, ValueError):
            errors[q.id] = "Réponse invalide : " + (
                "texte limité à 2000 caractères."
                if q.question_type == "text"
                else "choisissez une valeur de 1 à 5."
                if q.question_type == "scale"
                else "valeur non reconnue."
            )
    return answers, values, errors


@consultations_web_router.post(
    "/consultations/{consultation_id:int}/repondre",
    response_class=HTMLResponse,
    name="consultation_respond",
    include_in_schema=False,
    dependencies=[Depends(consultation_respond_rate_limit)],
)
async def consultation_respond(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    form_id = "form-reponse"
    back = f"/consultations/{consultation_id}"
    if not _is_citizen(viewer):
        message = (
            "Connectez-vous avec un compte citoyen pour répondre à cette consultation."
            if viewer is None
            else "Cette action est réservée aux comptes citoyens."
        )
        if _is_htmx(request):
            return _error_fragment(request, message, 422, form_id)
        return _message_page(request, message, 401 if viewer is None else 403, "/login")
    assert viewer is not None
    service = ConsultationService(session)
    try:
        consultation = await service.get_public(consultation_id)
    except ConsultationNotFoundError:
        return _message_page(request, "Consultation introuvable.", 404)

    form = await request.form()
    answers, values, errors = _read_answers(consultation, form)

    def fail(message: str, code: int, field_errors: dict[int, str] | None = None) -> Response:
        if _is_htmx(request):
            return _error_fragment(request, message, code, form_id)
        return _detail_response(
            request, consultation, viewer, values, field_errors or {}, banner=message, code=code
        )

    if errors:
        return fail("Certaines réponses sont invalides.", 422, errors)
    try:
        await service.submit(viewer, consultation_id, answers)
    except AnswerValidationError as exc:
        return fail(str(exc), 422, {exc.question_id: str(exc)})
    except ConsultationNotOpenError as exc:
        return fail(str(exc), 409)
    except _DOMAIN_ERRORS as exc:
        return fail(str(exc), _http_status(exc))
    target = f"{back}?info=repondu"
    if _is_htmx(request):
        return Response(status_code=200, headers={"HX-Redirect": target})
    return _redirect(target)


# --------------------------------------------------------------------------- #
# Résultats publics
# --------------------------------------------------------------------------- #
def _result_views(results: ConsultationResults) -> list[dict[str, Any]]:
    views: list[dict[str, Any]] = []
    for q in results.questions:
        counts = [{"option": c.option, "central": c.count} for c in (q.counts or [])]
        _bars(counts)
        views.append(
            {
                "position": q.position,
                "prompt": q.prompt,
                "question_type": q.question_type,
                "answer_count": q.answer_count,
                "withheld": q.withheld,
                "counts": counts,
                "has_counts": q.counts is not None,
                "mean": None if q.mean is None else format_number_fr(Decimal(str(q.mean)), 2),
                "texts": q.texts or [],
            }
        )
    return views


@consultations_web_router.get(
    "/consultations/{consultation_id:int}/resultats",
    response_class=HTMLResponse,
    name="consultation_results",
    include_in_schema=False,
)
async def consultation_results(
    request: Request, consultation_id: int, session: SessionDep
) -> HTMLResponse:
    try:
        consultation = await ConsultationService(session).get_public(consultation_id)
        results = await ConsultationResultsService(session).results(consultation_id)
    except ConsultationNotFoundError:
        return _message_page(
            request,
            "Résultats indisponibles : la consultation est introuvable ou n'est pas clôturée.",
            404,
            f"/consultations/{consultation_id}",
        )
    return templates.TemplateResponse(
        request,
        "public/consultation_results.html",
        page_context(
            request,
            consultation=consultation,
            results=results,
            questions=_result_views(results),
            type_labels=TYPE_LABELS,
            notice=results.notice or REPRESENTATIVITY_NOTICE,
        ),
    )


# --------------------------------------------------------------------------- #
# Administration
# --------------------------------------------------------------------------- #
async def _guard(
    request: Request, viewer: UserPublic | None, *roles: Role
) -> RedirectResponse | None:
    """Anonyme -> connexion ; rôle insuffisant -> 403 générique consigné."""
    if viewer is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    if not has_role(viewer.role, *roles):
        await _audit_admin_denied(request, viewer)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Action non autorisée.")
    return None


_ADMIN = (Role.CONTENT_ADMIN,)
_READERS = (Role.CONTENT_ADMIN, Role.MODERATOR)
ADMIN_BACK = "/admin/consultations"


async def _load(
    session: AsyncSession, consultation_id: int
) -> tuple[Consultation, list[ConsultationQuestion]]:
    consultation = (
        await session.execute(select(Consultation).where(Consultation.id == consultation_id))
    ).scalar_one_or_none()
    if consultation is None:
        raise ConsultationNotFoundError("Consultation introuvable.")
    questions = list(
        (
            await session.execute(
                select(ConsultationQuestion)
                .where(ConsultationQuestion.consultation_id == consultation_id)
                .order_by(ConsultationQuestion.position)
            )
        )
        .scalars()
        .all()
    )
    return consultation, questions


@consultations_web_router.get(
    "/admin/consultations",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultations",
    include_in_schema=False,
)
async def admin_list(request: Request, session: SessionDep, viewer: OptionalUser) -> Response:
    if (denied := await _guard(request, viewer, *_READERS)) is not None:
        return denied
    assert viewer is not None
    rows = (
        (await session.execute(select(Consultation).order_by(Consultation.id.desc())))
        .scalars()
        .all()
    )
    now = utcnow()
    items = [
        {
            "id": c.id,
            "title": c.title,
            "stored_status": c.status,
            "status": effective_status(c, now),
            "opens_at": c.opens_at,
            "closes_at": c.closes_at,
        }
        for c in rows
    ]
    return templates.TemplateResponse(
        request,
        "public/admin/consultations.html",
        page_context(
            request,
            items=items,
            status_labels=STATUS_LABELS,
            can_edit=has_role(viewer.role, *_ADMIN),
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
        ),
    )


def _slots(raw: str | None) -> int:
    try:
        value = int(raw or "")
    except ValueError:
        return DEFAULT_QUESTION_SLOTS
    return min(max(value, 1), MAX_QUESTIONS)


def _blank_question() -> dict[str, Any]:
    return {"prompt": "", "question_type": "single_choice", "required": False, "options_text": ""}


def _form_values_from_form(form: FormData, slots: int) -> dict[str, Any]:
    questions = []
    for i in range(1, slots + 1):
        question_type = str(form.get(f"q{i}_type", "single_choice"))
        questions.append(
            {
                "prompt": str(form.get(f"q{i}_prompt", "")),
                "question_type": question_type if question_type in _QUESTION_TYPES else "text",
                "required": form.get(f"q{i}_required") is not None,
                "options_text": str(form.get(f"q{i}_options", "")),
            }
        )
    return {
        "title": str(form.get("title", "")),
        "description": str(form.get("description", "")),
        "opens_at": str(form.get("opens_at", "")),
        "closes_at": str(form.get("closes_at", "")),
        "questions": questions,
    }


def _form_values_from_model(
    consultation: Consultation, questions: list[ConsultationQuestion]
) -> dict[str, Any]:
    def stamp(value: datetime) -> str:
        return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M")

    return {
        "title": consultation.title,
        "description": consultation.description,
        "opens_at": stamp(consultation.opens_at),
        "closes_at": stamp(consultation.closes_at),
        "questions": [
            {
                "prompt": q.prompt,
                "question_type": q.question_type,
                "required": bool(q.required),
                "options_text": "\n".join(q.options or []),
            }
            for q in questions
        ],
    }


def _parse_moment(raw: str, label: str, problems: list[str]) -> datetime | None:
    try:
        moment = datetime.fromisoformat(raw.strip())
    except ValueError:
        problems.append(f"{label} : saisissez une date et une heure valides.")
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def _describe(error: dict[str, Any]) -> str:
    loc = list(error.get("loc", ()))
    message = str(error.get("msg", "valeur invalide")).removeprefix("Value error, ")
    if len(loc) >= 2 and loc[0] == "questions" and isinstance(loc[1], int):
        field = str(loc[2]) if len(loc) > 2 else ""
        return (
            f"Question {loc[1] + 1} ({_FIELD_LABELS.get(field, field or 'question')}) : {message}"
        )
    field = str(loc[0]) if loc else ""
    return f"{_FIELD_LABELS.get(field, field or 'Consultation')} : {message}"


def _build_payload(values: dict[str, Any]) -> tuple[ConsultationCreate | None, list[str]]:
    problems: list[str] = []
    opens = _parse_moment(values["opens_at"], "Date d'ouverture", problems)
    closes = _parse_moment(values["closes_at"], "Date de clôture", problems)
    questions: list[dict[str, Any]] = []
    for q in values["questions"]:
        options = [line.strip() for line in q["options_text"].splitlines() if line.strip()]
        if not q["prompt"].strip() and not options:
            continue  # emplacement vide ignoré
        questions.append(
            {
                "prompt": q["prompt"],
                "question_type": q["question_type"],
                "required": q["required"],
                "options": options if q["question_type"] in CHOICE_TYPES else None,
            }
        )
    if problems:
        return None, problems
    try:
        payload = ConsultationCreate.model_validate(
            {
                "title": values["title"],
                "description": values["description"],
                "opens_at": opens,
                "closes_at": closes,
                "questions": questions,
            }
        )
    except ValidationError as exc:
        return None, [_describe(dict(e)) for e in exc.errors()]
    return payload, []


def _form_response(
    request: Request,
    values: dict[str, Any],
    *,
    consultation_id: int | None,
    problems: list[str] | None = None,
    code: int = 200,
) -> HTMLResponse:
    questions = list(values["questions"])
    wanted = _slots(request.query_params.get("questions")) if not questions else len(questions)
    questions += [_blank_question() for _ in range(max(0, wanted - len(questions)))]
    return templates.TemplateResponse(
        request,
        "public/admin/consultations_form.html",
        page_context(
            request,
            values={**values, "questions": questions},
            problems=problems or [],
            consultation_id=consultation_id,
            type_labels=TYPE_LABELS,
            max_questions=MAX_QUESTIONS,
        ),
        status_code=code,
    )


def _new_values(request: Request) -> dict[str, Any]:
    slots = _slots(request.query_params.get("questions"))
    return {
        "title": "",
        "description": "",
        "opens_at": "",
        "closes_at": "",
        "questions": [_blank_question() for _ in range(slots)],
    }


@consultations_web_router.get(
    "/admin/consultations/nouvelle",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_new",
    include_in_schema=False,
)
async def admin_new(request: Request, viewer: OptionalUser) -> Response:
    if (denied := await _guard(request, viewer, *_ADMIN)) is not None:
        return denied
    return _form_response(request, _new_values(request), consultation_id=None)


@consultations_web_router.post(
    "/admin/consultations/nouvelle",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_create",
    include_in_schema=False,
)
async def admin_create(request: Request, session: SessionDep, viewer: OptionalUser) -> Response:
    if (denied := await _guard(request, viewer, *_ADMIN)) is not None:
        return denied
    assert viewer is not None
    form = await request.form()
    values = _form_values_from_form(form, _slots(str(form.get("slots", ""))))
    payload, problems = _build_payload(values)
    if payload is None:
        return _form_response(request, values, consultation_id=None, problems=problems, code=422)
    try:
        consultation = await ConsultationService(session).create(viewer, payload)
    except _DOMAIN_ERRORS as exc:
        return _form_response(
            request, values, consultation_id=None, problems=[str(exc)], code=_http_status(exc)
        )
    return _redirect(f"{ADMIN_BACK}/{consultation.id}?info=creee")


@consultations_web_router.get(
    "/admin/consultations/{consultation_id:int}/modifier",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_edit",
    include_in_schema=False,
)
async def admin_edit(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    if (denied := await _guard(request, viewer, *_ADMIN)) is not None:
        return denied
    try:
        consultation, questions = await _load(session, consultation_id)
    except ConsultationNotFoundError:
        return _message_page(request, "Consultation introuvable.", 404, ADMIN_BACK)
    return _form_response(
        request,
        _form_values_from_model(consultation, questions),
        consultation_id=consultation_id,
    )


@consultations_web_router.post(
    "/admin/consultations/{consultation_id:int}/modifier",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_update",
    include_in_schema=False,
)
async def admin_update(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    if (denied := await _guard(request, viewer, *_ADMIN)) is not None:
        return denied
    assert viewer is not None
    form = await request.form()
    values = _form_values_from_form(form, _slots(str(form.get("slots", ""))))
    payload, problems = _build_payload(values)
    if payload is None:
        return _form_response(
            request, values, consultation_id=consultation_id, problems=problems, code=422
        )
    try:
        await ConsultationService(session).update(viewer, consultation_id, payload)
    except ConsultationNotFoundError:
        return _message_page(request, "Consultation introuvable.", 404, ADMIN_BACK)
    except _DOMAIN_ERRORS as exc:
        return _form_response(
            request,
            values,
            consultation_id=consultation_id,
            problems=[str(exc)],
            code=_http_status(exc),
        )
    return _redirect(f"{ADMIN_BACK}/{consultation_id}?info=modifiee")


@consultations_web_router.get(
    "/admin/consultations/{consultation_id:int}",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_detail",
    include_in_schema=False,
)
async def admin_detail(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    if (denied := await _guard(request, viewer, *_READERS)) is not None:
        return denied
    assert viewer is not None
    try:
        consultation, questions = await _load(session, consultation_id)
    except ConsultationNotFoundError:
        return _message_page(request, "Consultation introuvable.", 404, ADMIN_BACK)
    state = effective_status(consultation, utcnow())
    pending = (
        await ConsultationModerationService(session).pending(consultation_id)
        if state == "closed"
        else []
    )
    return templates.TemplateResponse(
        request,
        "public/admin/consultations_detail.html",
        page_context(
            request,
            consultation=consultation,
            questions=questions,
            state=state,
            pending=pending,
            status_labels=STATUS_LABELS,
            type_labels=TYPE_LABELS,
            can_edit=has_role(viewer.role, *_ADMIN),
            info=INFO_MESSAGES.get(request.query_params.get("info", "")),
        ),
    )


async def _lifecycle(
    request: Request,
    session: AsyncSession,
    viewer: UserPublic | None,
    consultation_id: int,
    action: str,
    info: str,
) -> Response:
    if (denied := await _guard(request, viewer, *_ADMIN)) is not None:
        return denied
    assert viewer is not None
    back = f"{ADMIN_BACK}/{consultation_id}"
    service = ConsultationService(session)
    try:
        if action == "publish":
            await service.publish(viewer, consultation_id)
        else:
            await service.close(viewer, consultation_id)
    except _DOMAIN_ERRORS as exc:
        return _message_page(request, str(exc), _http_status(exc), back)
    return _redirect(f"{back}?info={info}")


@consultations_web_router.post(
    "/admin/consultations/{consultation_id:int}/publier",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_publish",
    include_in_schema=False,
)
async def admin_publish(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    return await _lifecycle(request, session, viewer, consultation_id, "publish", "publiee")


@consultations_web_router.post(
    "/admin/consultations/{consultation_id:int}/cloturer",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_close",
    include_in_schema=False,
)
async def admin_close(
    request: Request, consultation_id: int, session: SessionDep, viewer: OptionalUser
) -> Response:
    return await _lifecycle(request, session, viewer, consultation_id, "close", "cloturee")


async def _review_text(
    request: Request,
    session: AsyncSession,
    viewer: UserPublic | None,
    consultation_id: int,
    answer_id: int,
    approve: bool,
) -> Response:
    if (denied := await _guard(request, viewer, *_READERS)) is not None:
        return denied
    assert viewer is not None
    back = f"{ADMIN_BACK}/{consultation_id}"
    service = ConsultationModerationService(session)
    try:
        if approve:
            await service.approve(viewer, answer_id)
        else:
            await service.reject(viewer, answer_id)
    except (ConsultationNotFoundError, ConsultationConflictError) as exc:
        return _message_page(request, str(exc), _http_status(exc), back)
    return _redirect(f"{back}?info={'texte-approuve' if approve else 'texte-rejete'}")


@consultations_web_router.post(
    "/admin/consultations/{consultation_id:int}/textes/{answer_id:int}/approuver",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_text_approve",
    include_in_schema=False,
)
async def admin_text_approve(
    request: Request,
    consultation_id: int,
    answer_id: int,
    session: SessionDep,
    viewer: OptionalUser,
) -> Response:
    return await _review_text(request, session, viewer, consultation_id, answer_id, True)


@consultations_web_router.post(
    "/admin/consultations/{consultation_id:int}/textes/{answer_id:int}/rejeter",
    response_class=HTMLResponse,
    response_model=None,
    name="admin_consultation_text_reject",
    include_in_schema=False,
)
async def admin_text_reject(
    request: Request,
    consultation_id: int,
    answer_id: int,
    session: SessionDep,
    viewer: OptionalUser,
) -> Response:
    return await _review_text(request, session, viewer, consultation_id, answer_id, False)
