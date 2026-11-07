"""Service des Consultations — gestion et lecture publique (Exigences 9.1, 9.6 à 9.9, 9.12).

* ``effective_status`` (pur) calcule le statut visible à partir du statut stocké et
  de l'horloge (D7) : la tâche Celery ne fait que persister la transition ;
* ``create`` / ``update`` / ``publish`` / ``close`` sont réservés aux
  Administrateurs_Contenu (le contrôle de rôle est fait par la couche API) ;
* ``close_expired`` persiste la clôture des consultations échues, avec un audit
  dont l'acteur est le système (``actor_id = NULL``) ;
* chaque publication, modification et clôture écrit **une** entrée d'audit dans la
  même transaction.

L'horloge est injectable pour les tests.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consultation import (
    Consultation,
    ConsultationAnswer,
    ConsultationParticipation,
    ConsultationQuestion,
    ConsultationResponse,
)
from app.schemas.consultation import (
    MAX_TEXT_LENGTH,
    AnswerIn,
    ConsultationCreate,
    ConsultationPublic,
    ConsultationSummary,
    QuestionIn,
    QuestionPublic,
)
from app.services.audit_service import AuditService

ENTITY_TYPE = "consultation"


def utcnow() -> datetime:
    """Horloge par défaut (UTC, avec fuseau)."""
    return datetime.now(UTC)


class ConsultationError(Exception):
    """Erreur générique du Service des Consultations."""


class ConsultationNotFoundError(ConsultationError):
    """Consultation inexistante (⇒ 404)."""


class ConsultationValidationError(ConsultationError):
    """Règle de validation violée, p. ex. ``closes_at`` passée à la publication (⇒ 422)."""


class ConsultationConflictError(ConsultationError):
    """Conflit d'état : réponses existantes, transition impossible (⇒ 409)."""


class ConsultationNotOpenError(ConsultationConflictError):
    """Consultation brouillon, programmée ou clôturée : ``state`` indique l'état (⇒ 409)."""

    def __init__(self, state: str) -> None:
        super().__init__(f"Consultation non ouverte aux réponses (état : {state}).")
        self.state = state


class AnswerValidationError(ConsultationValidationError):
    """Réponse invalide ; ``question_id`` identifie la question fautive (⇒ 422)."""

    def __init__(self, question_id: int, message: str) -> None:
        super().__init__(f"Question {question_id} : {message}")
        self.question_id = question_id


def _aware(value: datetime) -> datetime:
    """Rend un datetime comparable (un datetime naïf est interprété en UTC)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def effective_status(consultation: Consultation, now: datetime) -> str:
    """Statut effectif ``draft | scheduled | open | closed`` (fonction pure, D7)."""
    stored = consultation.status
    if stored == "draft":
        return "draft"
    now = _aware(now)
    if stored == "closed" or now >= _aware(consultation.closes_at):
        return "closed"
    if now < _aware(consultation.opens_at):
        return "scheduled"
    return "open"


def _snapshot(consultation: Consultation) -> dict[str, Any]:
    return {
        "title": consultation.title,
        "status": consultation.status,
        "opens_at": _aware(consultation.opens_at).isoformat(),
        "closes_at": _aware(consultation.closes_at).isoformat(),
    }


def _question_row(consultation_id: int, position: int, q: QuestionIn) -> ConsultationQuestion:
    return ConsultationQuestion(
        consultation_id=consultation_id,
        position=position,
        question_type=q.question_type,
        prompt=q.prompt,
        required=q.required,
        options=q.options,
    )


class ConsultationService:
    """Gestion du cycle de vie des Consultations."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime] = utcnow,
        audit: AuditService | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._audit = audit or AuditService(session)

    # ------------------------------------------------------------------ #
    # Gestion (content_admin)                                            #
    # ------------------------------------------------------------------ #
    async def create(self, actor: Any, data: ConsultationCreate) -> Consultation:
        """Crée une consultation au statut ``draft`` (Exigence 9.1)."""
        consultation = Consultation(
            title=data.title,
            description=data.description,
            status="draft",
            opens_at=data.opens_at,
            closes_at=data.closes_at,
            created_by_id=getattr(actor, "id", None),
        )
        self._session.add(consultation)
        await self._session.flush()
        self._session.add_all(
            _question_row(consultation.id, i, q) for i, q in enumerate(data.questions, start=1)
        )
        await self._session.flush()
        return consultation

    async def update(self, actor: Any, cid: int, data: ConsultationCreate) -> Consultation:
        """Modifie une consultation ; 409 si elle a déjà reçu des réponses (Exigence 9.8)."""
        consultation = await self._get_for_update(cid)
        if consultation.status == "closed":
            raise ConsultationConflictError("Consultation clôturée : modification impossible.")
        if await self._has_responses(cid):
            raise ConsultationConflictError(
                "La consultation a déjà reçu des réponses : modification impossible."
            )
        old = _snapshot(consultation)
        consultation.title = data.title
        consultation.description = data.description
        consultation.opens_at = data.opens_at
        consultation.closes_at = data.closes_at
        await self._session.execute(
            delete(ConsultationQuestion).where(ConsultationQuestion.consultation_id == cid)
        )
        await self._session.flush()
        self._session.add_all(
            _question_row(cid, i, q) for i, q in enumerate(data.questions, start=1)
        )
        await self._session.flush()
        await self._audit.record(
            action="consultation.update",
            entity_type=ENTITY_TYPE,
            entity_id=cid,
            old_data=old,
            new_data={**_snapshot(consultation), "question_count": len(data.questions)},
            actor_id=getattr(actor, "id", None),
        )
        return consultation

    async def publish(self, actor: Any, cid: int) -> Consultation:
        """Publie un brouillon ; 422 si ``closes_at`` est passée (Exigences 9.6, 9.7)."""
        consultation = await self._get_for_update(cid)
        if consultation.status != "draft":
            raise ConsultationConflictError("Seul un brouillon peut être publié.")
        if _aware(consultation.closes_at) <= _aware(self._clock()):
            raise ConsultationValidationError(
                "La date de clôture est passée : publication impossible."
            )
        old = _snapshot(consultation)
        consultation.status = "published"
        await self._session.flush()
        await self._audit.record(
            action="consultation.publish",
            entity_type=ENTITY_TYPE,
            entity_id=cid,
            old_data=old,
            new_data=_snapshot(consultation),
            actor_id=getattr(actor, "id", None),
        )
        return consultation

    async def close(self, actor: Any, cid: int) -> Consultation:
        """Clôture manuellement une consultation publiée (Exigence 9.12)."""
        consultation = await self._get_for_update(cid)
        if consultation.status != "published":
            raise ConsultationConflictError("Seule une consultation publiée peut être clôturée.")
        old = _snapshot(consultation)
        consultation.status = "closed"
        await self._session.flush()
        await self._audit.record(
            action="consultation.close",
            entity_type=ENTITY_TYPE,
            entity_id=cid,
            old_data=old,
            new_data=_snapshot(consultation),
            actor_id=getattr(actor, "id", None),
        )
        return consultation

    async def close_expired(self) -> int:
        """Persiste la clôture des consultations échues ; acteur système (Exigence 9.9)."""
        now = self._clock()
        result = await self._session.execute(
            select(Consultation)
            .where(Consultation.status == "published", Consultation.closes_at <= now)
            .with_for_update()
        )
        expired = list(result.scalars().all())
        for consultation in expired:
            old = _snapshot(consultation)
            consultation.status = "closed"
            await self._session.flush()
            await self._audit.record(
                action="consultation.auto_close",
                entity_type=ENTITY_TYPE,
                entity_id=consultation.id,
                old_data=old,
                new_data=_snapshot(consultation),
                actor_id=None,
            )
        return len(expired)

    # ------------------------------------------------------------------ #
    # Lecture publique                                                   #
    # ------------------------------------------------------------------ #
    async def list_public(self) -> list[ConsultationSummary]:
        """Consultations publiées ou clôturées, les plus récentes d'abord."""
        now = self._clock()
        result = await self._session.execute(
            select(Consultation)
            .where(Consultation.status.in_(("published", "closed")))
            .order_by(Consultation.opens_at.desc(), Consultation.id.desc())
        )
        return [self._summary(c, now) for c in result.scalars().all()]

    async def get_public(self, cid: int) -> ConsultationPublic:
        """Détail d'une consultation non brouillon avec ses questions (404 sinon)."""
        result = await self._session.execute(select(Consultation).where(Consultation.id == cid))
        consultation = result.scalar_one_or_none()
        if consultation is None or consultation.status == "draft":
            raise ConsultationNotFoundError("Consultation introuvable.")
        questions = await self._session.execute(
            select(ConsultationQuestion)
            .where(ConsultationQuestion.consultation_id == cid)
            .order_by(ConsultationQuestion.position)
        )
        summary = self._summary(consultation, self._clock())
        return ConsultationPublic(
            **summary.model_dump(),
            description=consultation.description,
            questions=[QuestionPublic.model_validate(q) for q in questions.scalars().all()],
        )

    # ------------------------------------------------------------------ #
    # Participation (citoyen)                                            #
    # ------------------------------------------------------------------ #
    async def submit(self, user: Any, cid: int, answers: list[AnswerIn]) -> None:
        """Enregistre la participation et la réponse anonyme en une transaction.

        Exigences 10.1 à 10.7, 10.10. La réponse n'a ni ``user_id`` ni horodatage (D6).
        """
        result = await self._session.execute(select(Consultation).where(Consultation.id == cid))
        consultation = result.scalar_one_or_none()
        if consultation is None:
            raise ConsultationNotFoundError("Consultation introuvable.")
        state = effective_status(consultation, self._clock())
        if state != "open":
            raise ConsultationNotOpenError(state)

        questions = list(
            (
                await self._session.execute(
                    select(ConsultationQuestion)
                    .where(ConsultationQuestion.consultation_id == cid)
                    .order_by(ConsultationQuestion.position)
                )
            )
            .scalars()
            .all()
        )
        rows = self._validate_answers(questions, answers)

        try:
            async with self._session.begin_nested():
                self._session.add(
                    ConsultationParticipation(
                        consultation_id=cid,
                        user_id=user.id,
                        participated_on=_aware(self._clock()).date(),
                    )
                )
                await self._session.flush()
        except IntegrityError as exc:
            raise ConsultationConflictError("Vous avez déjà répondu à cette consultation.") from exc

        response = ConsultationResponse(id=uuid.uuid4(), consultation_id=cid)
        self._session.add(response)
        await self._session.flush()
        self._session.add_all(ConsultationAnswer(response_id=response.id, **row) for row in rows)
        await self._session.flush()

    async def my_participations(self, user: Any) -> list[dict[str, Any]]:
        """Titre et date de participation uniquement, sans contenu (Exigence 10.11)."""
        result = await self._session.execute(
            select(Consultation.title, ConsultationParticipation.participated_on)
            .join(
                ConsultationParticipation,
                ConsultationParticipation.consultation_id == Consultation.id,
            )
            .where(ConsultationParticipation.user_id == user.id)
            .order_by(ConsultationParticipation.participated_on, Consultation.id)
        )
        return [{"title": title, "participated_on": day} for title, day in result.all()]

    @staticmethod
    def _validate_answers(
        questions: list[ConsultationQuestion], answers: list[AnswerIn]
    ) -> list[dict[str, Any]]:
        by_id = {q.id: q for q in questions}
        given: dict[int, AnswerIn] = {}
        for item in answers:
            if item.question_id not in by_id:
                raise AnswerValidationError(item.question_id, "question inexistante.")
            if item.question_id in given:
                raise AnswerValidationError(item.question_id, "réponse en double.")
            given[item.question_id] = item

        rows: list[dict[str, Any]] = []
        for q in questions:
            a: AnswerIn | None = given.get(q.id)
            row: dict[str, Any] = {"question_id": q.id}
            if q.question_type in ("single_choice", "multiple_choice"):
                selected = a.selected_options if a else None
                if selected:
                    allowed = set(q.options or [])
                    if any(s not in allowed for s in selected):
                        raise AnswerValidationError(q.id, "option inexistante.")
                    if len(set(selected)) != len(selected):
                        raise AnswerValidationError(q.id, "option répétée.")
                    if q.question_type == "single_choice" and len(selected) != 1:
                        raise AnswerValidationError(q.id, "une seule option est autorisée.")
                    row["selected_options"] = list(selected)
            elif q.question_type == "scale":
                value = a.scale_value if a else None
                if value is not None:
                    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
                        raise AnswerValidationError(q.id, "l'échelle doit être un entier de 1 à 5.")
                    row["scale_value"] = value
            else:  # text
                text_value = a.text_value if a else None
                if text_value is not None and text_value.strip():
                    if len(text_value) > MAX_TEXT_LENGTH:
                        raise AnswerValidationError(
                            q.id, f"texte limité à {MAX_TEXT_LENGTH} caractères."
                        )
                    row["text_value"] = text_value
                    row["text_review"] = "pending"
            if len(row) == 1:  # réponse vide
                if q.required:
                    raise AnswerValidationError(q.id, "réponse obligatoire manquante.")
                continue
            rows.append(row)
        return rows

    # ------------------------------------------------------------------ #
    # Internes                                                           #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _summary(consultation: Consultation, now: datetime) -> ConsultationSummary:
        return ConsultationSummary(
            id=consultation.id,
            title=consultation.title,
            status=effective_status(consultation, now),
            opens_at=consultation.opens_at,
            closes_at=consultation.closes_at,
        )

    async def _get_for_update(self, cid: int) -> Consultation:
        result = await self._session.execute(
            select(Consultation).where(Consultation.id == cid).with_for_update()
        )
        consultation = result.scalar_one_or_none()
        if consultation is None:
            raise ConsultationNotFoundError("Consultation introuvable.")
        return consultation

    async def _has_responses(self, cid: int) -> bool:
        result = await self._session.execute(
            select(ConsultationResponse.id)
            .where(ConsultationResponse.consultation_id == cid)
            .limit(1)
        )
        return result.first() is not None
