"""Service de modération des textes libres de Consultation (Exigences 11.7, 11.8).

* :meth:`ConsultationModerationService.pending` liste les textes ``pending`` d'une
  consultation **clôturée**, sans aucun lien vers l'utilisateur (la réponse est
  anonyme par construction : seul l'identifiant du texte est exposé) ;
* :meth:`approve` / :meth:`reject` fixent ``text_review`` à ``approved`` /
  ``rejected`` et écrivent **une** entrée d'audit (``consultation_text.approve`` /
  ``consultation_text.reject``) avec l'acteur, dans la même transaction.

Un texte rejeté n'est jamais supprimé : la ligne de réponse subsiste, il reste donc
compté dans le nombre de réponses, mais n'est jamais publié (Exigence 11.8).
Le garde de rôle (modérateur) est appliqué par la couche API.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.consultation import (
    Consultation,
    ConsultationAnswer,
    ConsultationQuestion,
)
from app.services.audit_service import AuditService
from app.services.consultation_service import (
    ConsultationConflictError,
    ConsultationNotFoundError,
    effective_status,
    utcnow,
)

AUDIT_ENTITY_TYPE: Final = "consultation_text"
ACTION_APPROVE: Final = "consultation_text.approve"
ACTION_REJECT: Final = "consultation_text.reject"


@dataclass(frozen=True, slots=True)
class PendingText:
    """Texte libre en attente de modération (aucun lien vers l'utilisateur)."""

    answer_id: int
    question_id: int
    prompt: str
    text: str


class ConsultationModerationService:
    """Modération individuelle des textes libres d'une consultation clôturée."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime] = utcnow,
        audit: AuditService | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._audit = audit or AuditService(session)

    async def pending(self, cid: int) -> list[PendingText]:
        """Textes ``pending`` d'une consultation clôturée ; 404 sinon."""
        consultation = await self._session.get(Consultation, cid)
        if consultation is None or effective_status(consultation, self._clock()) != "closed":
            raise ConsultationNotFoundError("Consultation introuvable ou non clôturée.")
        rows = (
            await self._session.execute(
                select(
                    ConsultationAnswer.id,
                    ConsultationQuestion.id,
                    ConsultationQuestion.prompt,
                    ConsultationAnswer.text_value,
                )
                .join(
                    ConsultationQuestion,
                    ConsultationQuestion.id == ConsultationAnswer.question_id,
                )
                .where(
                    ConsultationQuestion.consultation_id == cid,
                    ConsultationAnswer.text_review == "pending",
                )
                .order_by(ConsultationAnswer.id)
            )
        ).all()
        return [
            PendingText(answer_id=a, question_id=q, prompt=p, text=t or "") for a, q, p, t in rows
        ]

    async def approve(self, moderator: Any, answer_id: int) -> ConsultationAnswer:
        """``pending`` → ``approved`` (Exigence 11.7)."""
        return await self._review(moderator, answer_id, "approved", ACTION_APPROVE)

    async def reject(self, moderator: Any, answer_id: int) -> ConsultationAnswer:
        """``pending`` → ``rejected`` ; le texte reste compté (Exigence 11.8)."""
        return await self._review(moderator, answer_id, "rejected", ACTION_REJECT)

    async def _review(
        self, moderator: Any, answer_id: int, target: str, action: str
    ) -> ConsultationAnswer:
        answer = await self._session.get(ConsultationAnswer, answer_id)
        if answer is None or answer.text_review is None:
            raise ConsultationNotFoundError("Texte introuvable.")
        question = await self._session.get(ConsultationQuestion, answer.question_id)
        consultation = (
            await self._session.get(Consultation, question.consultation_id)
            if question is not None
            else None
        )
        if consultation is None or effective_status(consultation, self._clock()) != "closed":
            raise ConsultationNotFoundError("Texte introuvable.")
        if answer.text_review != "pending":
            raise ConsultationConflictError("Ce texte a déjà été modéré.")

        old = answer.text_review
        answer.text_review = target
        await self._session.flush()
        await self._audit.record(
            action=action,
            entity_type=AUDIT_ENTITY_TYPE,
            entity_id=answer.id,
            old_data={"text_review": old},
            new_data={
                "text_review": target,
                "consultation_id": consultation.id,
                "question_id": answer.question_id,
            },
            actor_id=getattr(moderator, "id", None),
        )
        return answer


__all__ = [
    "ACTION_APPROVE",
    "ACTION_REJECT",
    "ConsultationModerationService",
    "PendingText",
]
