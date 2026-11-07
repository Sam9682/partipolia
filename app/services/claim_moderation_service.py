"""Service de modération des Revendications (Exigences 8.6, 8.7, 8.8, 8.10, 8.11).

Contrat ``ClaimModerationService`` de la conception :

* ``hide`` — ``published`` → ``hidden`` (masquage humain) ;
* ``restore`` — ``hidden`` → ``published`` ; le compteur de signalements est remis
  à 0 et les signalements de la Revendication passent à ``is_active = false`` ;
* ``close`` — ``published`` | ``hidden`` → ``closed`` ;
* ``queue`` — contenus ``hidden`` en attente de modération.

Règles :

* le motif fait de 10 à 500 caractères (après ``strip``), sinon
  :class:`ClaimValidationError` (422) et le statut est conservé ;
* toute autre transition lève :class:`ClaimStateError` (409) ;
* chaque transition écrit **une** entrée d'audit (acteur, action, objet, motif)
  dans la même transaction ; le Service ne valide pas la transaction.

Le garde de rôle (``moderator`` / ``content_admin``, ``super_admin`` implicite) est
appliqué par la couche API.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import sanitize_text
from app.models.claim import Claim, ClaimComment, ContentReport
from app.services.audit_service import AuditService
from app.services.claim_service import (
    CLAIM_NOT_FOUND_ERROR,
    ClaimNotFoundError,
    ClaimStateError,
    ClaimValidationError,
)

ENTITY_TYPE: Final = "claim"
REASON_BOUNDS: Final = (10, 500)

REASON_LENGTH_ERROR: Final = "Le motif doit comporter de 10 à 500 caractères."
INVALID_TRANSITION_ERROR: Final = "Transition impossible depuis le statut courant."

# Machine à états : action → (statuts source, statut cible).
TRANSITIONS: Final[dict[str, tuple[tuple[str, ...], str]]] = {
    "hide": (("published",), "hidden"),
    "restore": (("hidden",), "published"),
    "close": (("published", "hidden"), "closed"),
}


@dataclass(frozen=True, slots=True)
class ModerationItem:
    """Contenu masqué en attente de modération."""

    kind: Literal["claim", "comment"]
    id: int
    claim_id: int
    excerpt: str
    report_count: int
    hidden_by: str | None
    moderation_reason: str | None
    created_at: datetime


def _clean_reason(reason: str) -> str:
    clean = sanitize_text(reason or "")
    if not REASON_BOUNDS[0] <= len(clean) <= REASON_BOUNDS[1]:
        raise ClaimValidationError(REASON_LENGTH_ERROR)
    return clean


def _snapshot(claim: Claim) -> dict[str, Any]:
    return {
        "status": claim.status,
        "report_count": claim.report_count,
        "hidden_by": claim.hidden_by,
        "moderation_pending": claim.moderation_pending,
    }


class ClaimModerationService:
    """Modération humaine des Revendications (Exigences 8.6 à 8.11)."""

    def __init__(self, session: AsyncSession, audit: AuditService | None = None) -> None:
        self._session = session
        self._audit = audit or AuditService(session)

    async def _get_for_update(self, claim_id: int) -> Claim:
        claim = await self._session.scalar(
            select(Claim)
            .where(Claim.id == claim_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if claim is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
        return claim

    async def _transition(self, moderator: Any, claim_id: int, reason: str, action: str) -> Claim:
        clean = _clean_reason(reason)  # motif invalide ⇒ statut conservé
        sources, target = TRANSITIONS[action]
        claim = await self._get_for_update(claim_id)
        if claim.status not in sources:
            raise ClaimStateError(INVALID_TRANSITION_ERROR)

        old = _snapshot(claim)
        claim.status = target
        claim.moderation_pending = False
        if action == "restore":
            claim.report_count = 0
            claim.hidden_by = None
            claim.moderation_reason = None
            await self._session.execute(
                update(ContentReport)
                .where(ContentReport.claim_id == claim_id, ContentReport.is_active.is_(True))
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
        else:
            claim.moderation_reason = clean
            if action == "hide":
                claim.hidden_by = "human"
        await self._session.flush()

        await self._audit.record(
            action=f"claim.{action}",
            entity_type=ENTITY_TYPE,
            entity_id=claim_id,
            old_data=old,
            new_data={**_snapshot(claim), "reason": clean},
            actor_id=getattr(moderator, "id", None),
        )
        return claim

    async def hide(self, moderator: Any, claim_id: int, reason: str) -> Claim:
        """``published`` → ``hidden`` (Exigences 8.8, 8.10)."""
        return await self._transition(moderator, claim_id, reason, "hide")

    async def restore(self, moderator: Any, claim_id: int, reason: str) -> Claim:
        """``hidden`` → ``published`` ; compteur à 0, signalements inactifs (Exigence 8.7)."""
        return await self._transition(moderator, claim_id, reason, "restore")

    async def close(self, moderator: Any, claim_id: int, reason: str) -> Claim:
        """``published`` | ``hidden`` → ``closed`` (Exigences 8.8, 8.10)."""
        return await self._transition(moderator, claim_id, reason, "close")

    async def queue(self) -> list[ModerationItem]:
        """Contenus ``hidden`` avec ``moderation_pending`` (Exigences 8.6, 8.11)."""
        claims = (
            await self._session.scalars(
                select(Claim)
                .where(Claim.status == "hidden", Claim.moderation_pending.is_(True))
                .execution_options(populate_existing=True)
            )
        ).all()
        comments = (
            await self._session.scalars(
                select(ClaimComment)
                .where(ClaimComment.status == "hidden", ClaimComment.moderation_pending.is_(True))
                .execution_options(populate_existing=True)
            )
        ).all()
        items = [
            ModerationItem(
                kind="claim",
                id=c.id,
                claim_id=c.id,
                excerpt=c.title,
                report_count=c.report_count,
                hidden_by=c.hidden_by,
                moderation_reason=c.moderation_reason,
                created_at=c.created_at,
            )
            for c in claims
        ] + [
            ModerationItem(
                kind="comment",
                id=m.id,
                claim_id=m.claim_id,
                excerpt=m.body[:200],
                report_count=m.report_count,
                hidden_by="auto",
                moderation_reason=None,
                created_at=m.created_at,
            )
            for m in comments
        ]
        items.sort(key=lambda i: (i.created_at, i.id))
        return items

    async def _comment_transition(
        self, moderator: Any, comment_id: int, reason: str, action: str
    ) -> ClaimComment:
        clean = _clean_reason(reason)
        source, target = ("visible", "hidden") if action == "hide" else ("hidden", "visible")
        comment = await self._session.scalar(
            select(ClaimComment)
            .where(ClaimComment.id == comment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if comment is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
        if comment.status != source:
            raise ClaimStateError(INVALID_TRANSITION_ERROR)

        old = {"status": comment.status, "report_count": comment.report_count}
        comment.status = target
        comment.moderation_pending = False
        if action == "restore":
            comment.report_count = 0
            await self._session.execute(
                update(ContentReport)
                .where(ContentReport.comment_id == comment_id, ContentReport.is_active.is_(True))
                .values(is_active=False)
                .execution_options(synchronize_session=False)
            )
        await self._session.flush()

        await self._audit.record(
            action=f"comment.{action}",
            entity_type="claim_comment",
            entity_id=comment_id,
            old_data=old,
            new_data={
                "status": comment.status,
                "report_count": comment.report_count,
                "reason": clean,
            },
            actor_id=getattr(moderator, "id", None),
        )
        return comment

    async def hide_comment(self, moderator: Any, comment_id: int, reason: str) -> ClaimComment:
        """Commentaire ``visible`` → ``hidden``."""
        return await self._comment_transition(moderator, comment_id, reason, "hide")

    async def restore_comment(self, moderator: Any, comment_id: int, reason: str) -> ClaimComment:
        """Commentaire ``hidden`` → ``visible`` ; compteur à 0, signalements inactifs."""
        return await self._comment_transition(moderator, comment_id, reason, "restore")
