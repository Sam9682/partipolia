"""Service des Revendications — cycle de vie (Exigences 6 et 8.12, 8.13).

Ce module implémente la partie « cycle de vie » du contrat ``ClaimService`` :

* ``create_draft`` — création au statut ``draft`` (Exigences 6.1, 6.4, 8.13) ;
* ``update`` — modification d'un brouillon, ou d'une Revendication publiée depuis
  moins de 15 minutes (horloge injectée, Exigences 6.6, 6.7) ;
* ``publish`` — transition conditionnelle ``UPDATE … WHERE status = 'draft'`` sous
  verrou de la ligne de l'utilisateur, avec quotas de 10 publications / 24 h et de
  50 Revendications au total (Exigences 6.2, 6.9) ;
* ``delete`` — suppression ; les soutiens, commentaires et signalements disparaissent
  par ``ON DELETE CASCADE`` (Exigence 6.8) ;
* ``mine`` — liste de l'auteur, avec le motif de masquage (Exigence 8.12).

Règles transverses :

* une Revendication inexistante **ou appartenant à autrui** lève
  :class:`ClaimNotFoundError` (404) avant toute autre vérification (Exigence 6.13) ;
* une violation de ``uq_claims_author_fingerprint`` est traduite en
  :class:`DuplicateClaimError` (422, « contenu déjà soumis », Exigence 8.13).

Correspondance HTTP : :class:`ClaimNotFoundError` → 404 ; :class:`ClaimValidationError`
(et ses sous-classes) → 422 ; :class:`ClaimStateError` → 409.

Le Service ne valide pas la transaction : l'appelant (la requête) commite.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import case, func, select, update
from sqlalchemy import delete as sa_delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.text import fingerprint as compute_fingerprint
from app.core.text import normalize_text, sanitize_text
from app.models.claim import Claim, ClaimComment, ClaimSupport, ContentReport
from app.models.consumption_category import ConsumptionCategory
from app.models.user import User
from app.schemas.claim import (
    ClaimCreate,
    ClaimDetail,
    ClaimItem,
    ClaimOwnerView,
    ClaimPage,
    CommentItem,
    ReportReason,
    SupportState,
)

# Messages génériques (Exigences 31.1, 31.2).
CLAIM_NOT_FOUND_ERROR: Final = "Revendication introuvable."
DUPLICATE_CLAIM_ERROR: Final = "Contenu déjà soumis."
EDIT_WINDOW_EXCEEDED_ERROR: Final = "Délai de modification dépassé."
QUOTA_DAILY_ERROR: Final = "Limite de 10 publications en 24 heures atteinte."
QUOTA_TOTAL_ERROR: Final = "Limite de 50 revendications atteinte."
NOT_EDITABLE_ERROR: Final = "Cette revendication ne peut plus être modifiée."
ALREADY_PUBLISHED_ERROR: Final = "Cette revendication n'est pas un brouillon."
UNKNOWN_CATEGORY_ERROR: Final = "Catégorie de consommation inconnue."

EDIT_WINDOW: Final = timedelta(minutes=15)
PUBLISH_WINDOW: Final = timedelta(hours=24)
MAX_PUBLISHED_PER_DAY: Final = 10
MAX_CLAIMS_PER_USER: Final = 50

TITLE_BOUNDS: Final = (5, 120)
BODY_BOUNDS: Final = (20, 2000)

_FINGERPRINT_CONSTRAINT: Final = "uq_claims_author_fingerprint"


def utcnow() -> datetime:
    """Horloge par défaut (UTC, avec fuseau)."""
    return datetime.now(UTC)


class ClaimError(Exception):
    """Erreur générique du Service des Revendications."""


class ClaimNotFoundError(ClaimError):
    """Revendication inexistante ou appartenant à autrui (⇒ 404, Exigence 6.13)."""


class ClaimValidationError(ClaimError):
    """Règle de validation violée (⇒ 422)."""


class DuplicateClaimError(ClaimValidationError):
    """Texte identique, après normalisation, à une soumission antérieure (⇒ 422)."""


class EditWindowExceededError(ClaimValidationError):
    """Modification refusée : 15 minutes écoulées depuis la publication (⇒ 422)."""


class PublicationQuotaError(ClaimValidationError):
    """Quota de publication atteint ; la Revendication reste ``draft`` (⇒ 422)."""


class ClaimStateError(ClaimError):
    """Transition impossible depuis le statut courant, p. ex. double publication (⇒ 409)."""


class ClaimConflictError(ClaimStateError):
    """Signalement en double du même citoyen sur la même cible (⇒ 409, Exigence 8.2)."""


CATALOGUE_SORTS: Final = ("recent", "supports", "category")
CATALOGUE_PAGE_SIZE: Final = 20
COMMENT_BOUNDS: Final = (1, 1000)
AUTO_HIDE_THRESHOLD: Final = 3

OWN_SUPPORT_ERROR: Final = "Vous ne pouvez pas soutenir votre propre revendication."
OWN_CONTENT_REPORT_ERROR: Final = "Vous ne pouvez pas signaler votre propre contenu."
DUPLICATE_REPORT_ERROR: Final = "Vous avez déjà signalé ce contenu."
INVALID_SORT_ERROR: Final = "Tri inconnu."
INVALID_PAGE_ERROR: Final = "Numéro de page invalide."
COMMENT_LENGTH_ERROR: Final = "Le commentaire doit comporter de 1 à 1000 caractères."
DUPLICATE_COMMENT_ERROR: Final = "Commentaire déjà soumis."

_COMMENT_FINGERPRINT_CONSTRAINT: Final = "uq_claim_comments_author_fingerprint"


@dataclass(frozen=True, slots=True)
class ReportTarget:
    """Cible d'un signalement : une Revendication ou un commentaire."""

    kind: Literal["claim", "comment"]
    id: int


def _aware(value: datetime) -> datetime:
    """Rend un datetime comparable (UTC) même s'il est naïf."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_fingerprint_violation(exc: IntegrityError) -> bool:
    return _FINGERPRINT_CONSTRAINT in str(exc.orig) or _FINGERPRINT_CONSTRAINT in str(exc)


class ClaimService:
    """Cycle de vie des Revendications d'un citoyen (Exigences 6, 8.12, 8.13)."""

    def __init__(
        self,
        session: AsyncSession,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._session = session
        self._clock = clock

    # ------------------------------------------------------------------ #
    # Aides internes                                                      #
    # ------------------------------------------------------------------ #
    async def _get_owned(self, author_id: int, claim_id: int) -> Claim:
        """Charge la Revendication de l'auteur ; 404 si absente ou d'autrui."""
        claim = await self._session.scalar(
            select(Claim)
            .where(Claim.id == claim_id, Claim.author_id == author_id)
            .execution_options(populate_existing=True)
        )
        if claim is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
        return claim

    async def _category_id(self, code: str | None) -> int | None:
        if code is None:
            return None
        category_id = await self._session.scalar(
            select(ConsumptionCategory.id).where(ConsumptionCategory.code == code)
        )
        if category_id is None:
            raise ClaimValidationError(UNKNOWN_CATEGORY_ERROR)
        return category_id

    @staticmethod
    def _clean(data: ClaimCreate) -> tuple[str, str]:
        """Assainit titre et texte, puis revérifie les bornes (Exigences 6.4, 6.5)."""
        title = sanitize_text(data.title)
        body = sanitize_text(data.body)
        if not TITLE_BOUNDS[0] <= len(title) <= TITLE_BOUNDS[1]:
            raise ClaimValidationError("Le titre doit comporter de 5 à 120 caractères.")
        if not BODY_BOUNDS[0] <= len(body) <= BODY_BOUNDS[1]:
            raise ClaimValidationError("Le texte doit comporter de 20 à 2000 caractères.")
        return title, body

    async def _flush_unique(self) -> None:
        """Flush dans un SAVEPOINT ; traduit la violation d'empreinte en 422."""
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError as exc:
            if _is_fingerprint_violation(exc):
                raise DuplicateClaimError(DUPLICATE_CLAIM_ERROR) from exc
            raise

    # ------------------------------------------------------------------ #
    # Création (Exigences 6.1, 6.4, 8.13)                                 #
    # ------------------------------------------------------------------ #
    async def create_draft(self, author_id: int, data: ClaimCreate) -> Claim:
        """Crée une Revendication au statut ``draft``, visible de son seul auteur."""
        title, body = self._clean(data)
        category_id = await self._category_id(data.category_code)
        claim = Claim(
            author_id=author_id,
            category_id=category_id,
            title=title,
            body=body,
            fingerprint=compute_fingerprint(title, body),
            status="draft",
        )
        self._session.add(claim)
        try:
            await self._flush_unique()
        except ClaimError:
            self._session.expunge(claim)
            raise
        await self._session.refresh(claim)
        return claim

    # ------------------------------------------------------------------ #
    # Modification (Exigences 6.6, 6.7)                                   #
    # ------------------------------------------------------------------ #
    async def update(self, author_id: int, claim_id: int, data: ClaimCreate) -> Claim:
        """Modifie un brouillon, ou une Revendication publiée depuis < 15 minutes."""
        claim = await self._get_owned(author_id, claim_id)  # 404 avant toute autre règle

        if claim.status == "published":
            if claim.published_at is None or (
                self._clock() - _aware(claim.published_at) >= EDIT_WINDOW
            ):
                raise EditWindowExceededError(EDIT_WINDOW_EXCEEDED_ERROR)
        elif claim.status != "draft":
            raise ClaimValidationError(NOT_EDITABLE_ERROR)

        title, body = self._clean(data)
        category_id = await self._category_id(data.category_code)

        claim.title = title
        claim.body = body
        claim.category_id = category_id
        claim.fingerprint = compute_fingerprint(title, body)
        try:
            await self._flush_unique()
        except ClaimError:
            await self._session.refresh(claim)  # conserve le texte précédent
            raise
        await self._session.refresh(claim)
        return claim

    # ------------------------------------------------------------------ #
    # Publication (Exigences 6.2, 6.9)                                    #
    # ------------------------------------------------------------------ #
    async def publish(self, author_id: int, claim_id: int) -> Claim:
        """Publie un brouillon sous verrou de l'utilisateur, quotas vérifiés."""
        claim = await self._get_owned(author_id, claim_id)  # 404 avant toute autre règle

        # Verrou de ligne de l'utilisateur : sérialise les publications concurrentes.
        await self._session.execute(select(User.id).where(User.id == author_id).with_for_update())

        now = self._clock()
        since = now - PUBLISH_WINDOW
        published_24h = await self._session.scalar(
            select(func.count(Claim.id)).where(
                Claim.author_id == author_id, Claim.published_at > since
            )
        )
        if (published_24h or 0) >= MAX_PUBLISHED_PER_DAY:
            raise PublicationQuotaError(QUOTA_DAILY_ERROR)

        # Le brouillon à publier compte parmi les Revendications possédées.
        total = await self._session.scalar(
            select(func.count(Claim.id)).where(Claim.author_id == author_id)
        )
        if (total or 0) > MAX_CLAIMS_PER_USER:
            raise PublicationQuotaError(QUOTA_TOTAL_ERROR)

        # Transition conditionnelle : 0 ligne ⇒ déjà publiée (course ou double clic).
        result = await self._session.execute(
            update(Claim)
            .where(
                Claim.id == claim_id,
                Claim.author_id == author_id,
                Claim.status == "draft",
            )
            .values(status="published", published_at=now)
            .returning(Claim.id)
            .execution_options(synchronize_session=False)
        )
        if result.scalar_one_or_none() is None:
            raise ClaimStateError(ALREADY_PUBLISHED_ERROR)

        await self._session.refresh(claim)
        return claim

    # ------------------------------------------------------------------ #
    # Suppression (Exigence 6.8)                                          #
    # ------------------------------------------------------------------ #
    async def delete(self, author_id: int, claim_id: int) -> None:
        """Supprime la Revendication ; soutiens et commentaires partent en cascade."""
        claim = await self._get_owned(author_id, claim_id)  # 404 avant toute autre règle
        await self._session.execute(
            sa_delete(Claim)
            .where(Claim.id == claim_id, Claim.author_id == author_id)
            .execution_options(synchronize_session=False)
        )
        self._session.expunge(claim)

    # ------------------------------------------------------------------ #
    # Mes revendications (Exigence 8.12)                                  #
    # ------------------------------------------------------------------ #
    async def mine(self, author_id: int) -> list[ClaimOwnerView]:
        """Revendications de l'auteur ; le motif de masquage est visible si masquée."""
        rows = await self._session.execute(
            select(Claim, ConsumptionCategory.code)
            .outerjoin(ConsumptionCategory, Claim.category_id == ConsumptionCategory.id)
            .where(Claim.author_id == author_id)
            .order_by(Claim.id.desc())
            .execution_options(populate_existing=True)
        )
        return [
            ClaimOwnerView(
                id=claim.id,
                title=claim.title,
                body=claim.body,
                category_code=category_code,
                status=claim.status,
                support_count=claim.support_count,
                published_at=claim.published_at,
                created_at=claim.created_at,
                moderation_reason=claim.moderation_reason if claim.status == "hidden" else None,
            )
            for claim, category_code in rows.all()
        ]

    # ------------------------------------------------------------------ #
    # Soutiens (Exigences 7.1 à 7.7)                                      #
    # ------------------------------------------------------------------ #
    async def _published_author(self, claim_id: int) -> int:
        """Auteur d'une Revendication publiée ; 404 sinon (Exigence 7.7)."""
        author_id = await self._session.scalar(
            select(Claim.author_id).where(Claim.id == claim_id, Claim.status == "published")
        )
        if author_id is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
        return author_id

    async def _support_count(self, claim_id: int) -> int:
        count = await self._session.scalar(select(Claim.support_count).where(Claim.id == claim_id))
        return int(count or 0)

    async def support(self, user_id: int, claim_id: int) -> SupportState:
        """Soutient une Revendication publiée (idempotent, auteur interdit)."""
        author_id = await self._published_author(claim_id)
        if author_id == user_id:
            raise ClaimValidationError(OWN_SUPPORT_ERROR)

        inserted = await self._session.scalar(
            pg_insert(ClaimSupport)
            .values(claim_id=claim_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=["claim_id", "user_id"])
            .returning(ClaimSupport.user_id)
        )
        if inserted is None:
            return SupportState(supported=True, support_count=await self._support_count(claim_id))

        count = await self._session.scalar(
            update(Claim)
            .where(Claim.id == claim_id)
            .values(support_count=Claim.support_count + 1)
            .returning(Claim.support_count)
            .execution_options(synchronize_session=False)
        )
        return SupportState(supported=True, support_count=int(count or 0))

    async def unsupport(self, user_id: int, claim_id: int) -> SupportState:
        """Retire le soutien (idempotent, compteur jamais inférieur à 0)."""
        await self._published_author(claim_id)

        deleted = await self._session.scalar(
            sa_delete(ClaimSupport)
            .where(ClaimSupport.claim_id == claim_id, ClaimSupport.user_id == user_id)
            .returning(ClaimSupport.user_id)
            .execution_options(synchronize_session=False)
        )
        if deleted is None:
            return SupportState(supported=False, support_count=await self._support_count(claim_id))

        count = await self._session.scalar(
            update(Claim)
            .where(Claim.id == claim_id)
            .values(support_count=func.greatest(Claim.support_count - 1, 0))
            .returning(Claim.support_count)
            .execution_options(synchronize_session=False)
        )
        return SupportState(supported=False, support_count=int(count or 0))

    # ------------------------------------------------------------------ #
    # Catalogue (Exigences 7.9 à 7.12)                                    #
    # ------------------------------------------------------------------ #
    async def catalogue(
        self,
        viewer_id: int | None,
        sort: str = "recent",
        page: int = 1,
    ) -> ClaimPage:
        """Page du catalogue des Revendications publiées, en au plus 3 requêtes SQL."""
        if sort not in CATALOGUE_SORTS:
            raise ClaimValidationError(INVALID_SORT_ERROR)
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ClaimValidationError(INVALID_PAGE_ERROR)

        order: tuple[Any, ...]
        if sort == "supports":
            order = (Claim.support_count.desc(), Claim.id.desc())
        elif sort == "category":
            order = (ConsumptionCategory.name.asc().nulls_last(), Claim.id.desc())
        else:
            order = (Claim.published_at.desc(), Claim.id.desc())

        total = int(
            await self._session.scalar(
                select(func.count(Claim.id)).where(Claim.status == "published")
            )
            or 0
        )
        rows = (
            await self._session.execute(
                select(
                    Claim.id,
                    Claim.title,
                    Claim.body,
                    ConsumptionCategory.code,
                    ConsumptionCategory.name,
                    User.display_name,
                    Claim.support_count,
                    Claim.published_at,
                )
                .join(User, User.id == Claim.author_id)
                .outerjoin(ConsumptionCategory, ConsumptionCategory.id == Claim.category_id)
                .where(Claim.status == "published")
                .order_by(*order)
                .limit(CATALOGUE_PAGE_SIZE)
                .offset((page - 1) * CATALOGUE_PAGE_SIZE)
            )
        ).all()

        supported: set[int] = set()
        if viewer_id is not None and rows:
            supported = set(
                (
                    await self._session.scalars(
                        select(ClaimSupport.claim_id).where(
                            ClaimSupport.user_id == viewer_id,
                            ClaimSupport.claim_id.in_([r.id for r in rows]),
                        )
                    )
                ).all()
            )

        items = [
            ClaimItem(
                id=r.id,
                title=r.title,
                body=r.body,
                category_code=r.code,
                category_label=r.name,
                author_pseudonym=r.display_name,
                support_count=r.support_count,
                supported_by_me=r.id in supported,
                published_at=r.published_at,
            )
            for r in rows
        ]
        return ClaimPage(
            items=items,
            total=total,
            page=page,
            limit=CATALOGUE_PAGE_SIZE,
            pages=-(-total // CATALOGUE_PAGE_SIZE),
        )

    # ------------------------------------------------------------------ #
    # Commentaires (Exigence 7.16)                                        #
    # ------------------------------------------------------------------ #
    async def comment(self, user_id: int, claim_id: int, body: str) -> ClaimComment:
        """Ajoute un commentaire (1..1000 caractères) à une Revendication publiée."""
        await self._published_author(claim_id)
        clean = sanitize_text(body)
        if not COMMENT_BOUNDS[0] <= len(clean) <= COMMENT_BOUNDS[1]:
            raise ClaimValidationError(COMMENT_LENGTH_ERROR)

        comment = ClaimComment(
            claim_id=claim_id,
            author_id=user_id,
            body=clean,
            fingerprint=hashlib.sha256(normalize_text(clean).encode("utf-8")).hexdigest(),
        )
        self._session.add(comment)
        try:
            async with self._session.begin_nested():
                await self._session.flush()
        except IntegrityError as exc:
            self._session.expunge(comment)
            if _COMMENT_FINGERPRINT_CONSTRAINT in str(exc):
                raise ClaimValidationError(DUPLICATE_COMMENT_ERROR) from exc
            raise
        await self._session.refresh(comment)
        return comment

    # ------------------------------------------------------------------ #
    # Signalements (Exigences 8.1 à 8.3, 8.5, 8.6)                        #
    # ------------------------------------------------------------------ #
    async def ensure_comment_in_claim(self, claim_id: int, comment_id: int) -> None:
        """Vérifie que le commentaire appartient à la Revendication ; 404 sinon."""
        found = await self._session.scalar(
            select(ClaimComment.id).where(
                ClaimComment.id == comment_id, ClaimComment.claim_id == claim_id
            )
        )
        if found is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)

    async def report(self, user_id: int, target: ReportTarget, reason: ReportReason) -> None:
        """Signale une Revendication ou un commentaire ; masque au 3e signalement actif."""
        if target.kind == "claim":
            author_id = await self._published_author(target.id)
        else:
            row = (
                await self._session.execute(
                    select(ClaimComment.author_id)
                    .join(Claim, Claim.id == ClaimComment.claim_id)
                    .where(
                        ClaimComment.id == target.id,
                        ClaimComment.status == "visible",
                        Claim.status == "published",
                    )
                )
            ).first()
            if row is None:
                raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
            author_id = row[0]
        if author_id == user_id:
            raise ClaimValidationError(OWN_CONTENT_REPORT_ERROR)

        is_claim = target.kind == "claim"
        insert_stmt = pg_insert(ContentReport).values(
            reporter_id=user_id,
            claim_id=target.id if is_claim else None,
            comment_id=None if is_claim else target.id,
            reason=reason,
        )
        stmt = insert_stmt.on_conflict_do_nothing(
            index_elements=["reporter_id", "claim_id" if is_claim else "comment_id"],
            index_where=(
                ContentReport.claim_id.is_not(None)
                if is_claim
                else ContentReport.comment_id.is_not(None)
            ),
        ).returning(ContentReport.id)
        if await self._session.scalar(stmt) is None:
            raise ClaimConflictError(DUPLICATE_REPORT_ERROR)

        # Incrément et masquage dans une seule instruction : atomique sous verrou de ligne.
        if is_claim:
            reached = (Claim.report_count + 1 >= AUTO_HIDE_THRESHOLD) & (
                Claim.status == "published"
            )
            await self._session.execute(
                update(Claim)
                .where(Claim.id == target.id)
                .values(
                    report_count=Claim.report_count + 1,
                    status=case((reached, "hidden"), else_=Claim.status),
                    hidden_by=case((reached, "auto"), else_=Claim.hidden_by),
                    moderation_pending=case((reached, True), else_=Claim.moderation_pending),
                )
                .execution_options(synchronize_session=False)
            )
        else:
            reached = (ClaimComment.report_count + 1 >= AUTO_HIDE_THRESHOLD) & (
                ClaimComment.status == "visible"
            )
            await self._session.execute(
                update(ClaimComment)
                .where(ClaimComment.id == target.id)
                .values(
                    report_count=ClaimComment.report_count + 1,
                    status=case((reached, "hidden"), else_=ClaimComment.status),
                    moderation_pending=case((reached, True), else_=ClaimComment.moderation_pending),
                )
                .execution_options(synchronize_session=False)
            )

    # ------------------------------------------------------------------ #
    # Détail et lecture des commentaires                                  #
    # ------------------------------------------------------------------ #
    async def get_detail(
        self, viewer_id: int | None, claim_id: int, *, can_moderate: bool = False
    ) -> ClaimDetail:
        """Détail d'une Revendication ; 404 si non publiée, sauf auteur ou modération."""
        row = (
            await self._session.execute(
                select(
                    Claim.id,
                    Claim.title,
                    Claim.body,
                    Claim.status,
                    Claim.author_id,
                    Claim.moderation_reason,
                    ConsumptionCategory.code,
                    ConsumptionCategory.name,
                    User.display_name,
                    Claim.support_count,
                    Claim.published_at,
                )
                .join(User, User.id == Claim.author_id)
                .outerjoin(ConsumptionCategory, ConsumptionCategory.id == Claim.category_id)
                .where(Claim.id == claim_id)
            )
        ).first()
        if row is None:
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)
        is_author = viewer_id is not None and row.author_id == viewer_id
        if row.status != "published" and not (is_author or can_moderate):
            raise ClaimNotFoundError(CLAIM_NOT_FOUND_ERROR)

        supported = False
        if viewer_id is not None:
            supported = (
                await self._session.scalar(
                    select(ClaimSupport.user_id).where(
                        ClaimSupport.claim_id == claim_id, ClaimSupport.user_id == viewer_id
                    )
                )
                is not None
            )
        return ClaimDetail(
            id=row.id,
            title=row.title,
            body=row.body,
            category_code=row.code,
            category_label=row.name,
            author_pseudonym=row.display_name,
            support_count=row.support_count,
            supported_by_me=supported,
            published_at=row.published_at,
            status=row.status,
            moderation_reason=row.moderation_reason if (is_author or can_moderate) else None,
        )

    async def list_comments(self, claim_id: int) -> list[CommentItem]:
        """Commentaires visibles d'une Revendication publiée (404 sinon)."""
        await self._published_author(claim_id)
        rows = (
            await self._session.execute(
                select(
                    ClaimComment.id,
                    ClaimComment.claim_id,
                    ClaimComment.body,
                    ClaimComment.created_at,
                    User.display_name,
                )
                .join(User, User.id == ClaimComment.author_id)
                .where(ClaimComment.claim_id == claim_id, ClaimComment.status == "visible")
                .order_by(ClaimComment.id.asc())
            )
        ).all()
        return [
            CommentItem(
                id=r.id,
                claim_id=r.claim_id,
                body=r.body,
                author_pseudonym=r.display_name,
                created_at=r.created_at,
            )
            for r in rows
        ]
