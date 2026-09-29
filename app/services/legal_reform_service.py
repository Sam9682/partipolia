"""Service d'orchestration du domaine « Réparer la loi » (Exigences 1, 2, 4, 8–10, 13.8).

Ce module fournit :class:`LegalReformService`, qui orchestre le domaine « Réparer la
loi » **sans dupliquer** les responsabilités des Services existants : il **compose**
``proposal_service``, ``vote_service``, ``argument_service``, ``source_service``,
``theme_service``, ``popularity_service`` et ``moderation_service`` (Exigence 13.8).

Portée de cette tâche (4.1) — **squelette + lecture et création des Problèmes
Juridiques** uniquement :

* ``list_problems`` — liste paginée bornée (défaut 20, max 100) avec ``total``
  (Exigences 1.4, 14.1) ;
* ``get_problem`` — détail d'un Problème_Juridique, **404 si absent** (Exigences
  1.5, 1.6, 14.2, 14.3) ;
* ``get_problem_by_slug`` — même détail par ``slug`` (404 si absent) ;
* ``create_problem`` — création (Administrateur) d'un Problème_Juridique avec un
  ``slug`` **unique** et un ``id`` **unique** attribués (Exigences 2.1, 2.4, 2.6).

Le rattachement des Réformes et le vote citoyen (4.3, 4.5) ainsi que l'amendement,
la prise de position et le signalement d'effet secondaire (4.8) délèguent
respectivement à ``proposal_service``, ``argument_service`` et ``moderation_service``
sans dupliquer leur logique (Exigence 13.8).

Le Service est instancié par requête avec une ``AsyncSession`` ; SQLAlchemy 2.x en
style asynchrone, avec chargement anticipé ``selectin`` sur les relations (défini au
niveau des modèles) pour éviter le N+1.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.argument import Argument
from app.models.comment import Comment
from app.models.legal_problem import LegalProblem
from app.models.legal_problem_reform import LegalProblemReform
from app.models.proposal import ProposalVersion
from app.models.side_effect_report import SideEffectReport
from app.models.user import User
from app.schemas.common import Page
from app.schemas.legal_problem import (
    MAX_CONTRIBUTION_LENGTH,
    AmendmentInput,
    LegalProblemCreate,
    LegalProblemDetail,
    LegalProblemSummary,
    ReformView,
    VoteCounts,
)
from app.schemas.moderation import ModerationClassification
from app.schemas.proposal import ProposalUpdate
from app.services.argument_service import ArgumentService
from app.services.audit_service import AuditService
from app.services.moderation_service import MODERATION_AUDIT_ACTION, ModerationService
from app.services.popularity_service import PopularityService
from app.services.proposal_service import ProposalService
from app.services.vote_service import VoteService

# Pagination bornée exposée par « Réparer la loi » (Exigences 1.4, 14.1).
DEFAULT_PAGE_LIMIT: Final = 20
MAX_PAGE_LIMIT: Final = 100

# Longueur maximale de la portion « titre » d'un slug avant le suffixe éventuel.
_SLUG_MAX_LENGTH: Final = 200

# Bornes de cardinalité des Réformes_Proposées rattachées (Exigences 4.1, 4.2).
MIN_REFORMS: Final = 3
MAX_REFORMS: Final = 5
STATUS_QUO_COUNT: Final = 1

# Messages d'erreur.
NOT_FOUND_ERROR: Final = "Problème juridique introuvable."
FORBIDDEN_ERROR: Final = "Action réservée à un administrateur."
CARDINALITY_ERROR: Final = (
    "Cardinalité des Réformes_Proposées invalide : le Problème_Juridique doit "
    "compter entre 3 et 5 Réformes dont exactement une Option_Statu_Quo."
)
CONTRIBUTION_LENGTH_ERROR: Final = "Le contenu doit comporter entre 1 et 5000 caractères."

# Bornes de longueur d'une contribution libre (amendement, signalement — Exigence 8.9).
MIN_CONTRIBUTION_LENGTH: Final = 1

# Statut appliqué à un Signalement selon la classification déléguée (Exigence 15.1).
_SIDE_EFFECT_STATUS_VISIBLE: Final = "VISIBLE"
_SIDE_EFFECT_STATUS_PENDING: Final = "PENDING"

# ``entity_type`` de l'entité auditée pour la décision de modération d'un
# Signalement_D_Effet_Secondaire (Exigence 15.3, cohérent avec COMMENT_MODERATED).
_SIDE_EFFECT_AUDIT_ENTITY_TYPE: Final = "SideEffectReport"
# Origine de la décision consignée : la classification est automatique (Exigence 15.3).
_SIDE_EFFECT_AUDIT_ORIGIN: Final = "automatique"


class LegalReformError(Exception):
    """Erreur générique du Service « Réparer la loi »."""


class LegalProblemNotFoundError(LegalReformError):
    """Le Problème_Juridique demandé n'existe pas (⇒ 404, Exigences 1.6, 14.3)."""


class LegalReformPermissionError(LegalReformError):
    """Action réservée à un Administrateur (⇒ 403, Exigence 2.1)."""


class ContributionLengthError(LegalReformError):
    """Longueur d'une contribution libre hors ``[1, 5000]`` (⇒ 422, Exigence 8.9).

    Levée lorsqu'un Amendement ou un Signalement_D_Effet_Secondaire porte un
    contenu vide ou de plus de 5000 caractères (après normalisation) ; l'état de
    la Plateforme reste inchangé (Exigence 8.9, Property 6).
    """


class ReformCardinalityError(LegalReformError):
    """Cardinalité des Réformes_Proposées invalide (⇒ 422, Exigences 4.1, 4.2).

    Levée lorsqu'une configuration de Réformes rattachées à un Problème_Juridique
    ne respecte pas ``3 ≤ nombre ≤ 5`` **et** exactement une Option_Statu_Quo.
    L'état du Problème_Juridique reste inchangé (Exigence 4.2, Property 3).
    """


class LegalReformService:
    """Orchestration du domaine « Réparer la loi ».

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Problèmes Juridiques — lecture (Exigences 1.4–1.6, 14.1–14.3)        #
    # ------------------------------------------------------------------ #
    async def list_problems(
        self, *, page: int = 1, limit: int = DEFAULT_PAGE_LIMIT
    ) -> Page[LegalProblemSummary]:
        """Liste paginée bornée des Problèmes_Juridiques (Exigences 1.4, 14.1).

        ``page`` est ramené à ``1`` au minimum ; ``limit`` est borné à l'intervalle
        ``[1, 100]`` (défaut 20). L'enveloppe :class:`Page` expose ``total``, le
        nombre total de Problèmes_Juridiques correspondants (Exigence 14.1).
        """
        page = max(page, 1)
        limit = self._clamp_limit(limit)

        total = int(
            (await self._session.scalar(select(func.count()).select_from(LegalProblem))) or 0
        )

        stmt = (
            select(LegalProblem)
            .order_by(LegalProblem.created_at.desc(), LegalProblem.id.desc())
            .offset((page - 1) * limit)
            .limit(limit)
        )
        rows = (await self._session.scalars(stmt)).all()
        items = [LegalProblemSummary.model_validate(row) for row in rows]
        return Page.create(items, total=total, page=page, limit=limit)

    async def get_problem(self, problem_id: int) -> LegalProblemDetail:
        """Retourne le détail d'un Problème_Juridique, Réformes comprises (Exigence 4.4).

        Lève :class:`LegalProblemNotFoundError` si l'identifiant n'existe pas
        (⇒ 404, Exigences 1.6, 14.3).
        """
        problem = await self._get_or_404(problem_id)
        return self._to_detail(problem)

    async def get_problem_by_slug(self, slug: str) -> LegalProblemDetail:
        """Retourne le détail d'un Problème_Juridique par son ``slug`` (Exigence 4.4).

        Lève :class:`LegalProblemNotFoundError` si le ``slug`` n'existe pas (⇒ 404).
        """
        problem = await self._session.scalar(select(LegalProblem).where(LegalProblem.slug == slug))
        if problem is None:
            raise LegalProblemNotFoundError(NOT_FOUND_ERROR)
        return self._to_detail(problem)

    # ------------------------------------------------------------------ #
    # Problèmes Juridiques — création (Exigences 2.1, 2.4, 2.6)            #
    # ------------------------------------------------------------------ #
    async def create_problem(self, admin: User, data: LegalProblemCreate) -> LegalProblem:
        """Crée un Problème_Juridique avec un ``slug`` et un ``id`` uniques (Exigence 2.6).

        Réservé aux Administrateurs (Exigence 2.1) : toute autre tentative lève
        :class:`LegalReformPermissionError`. Le ``slug`` est dérivé du titre et rendu
        **unique** en base ; l'``id`` unique est attribué par la base à l'insertion.
        """
        self._authorize_admin(admin)

        slug = await self._make_unique_slug(data.title)
        problem = LegalProblem(
            slug=slug,
            title=data.title,
            summary=data.summary,
            theme_id=data.theme_id,
            affected_citizens_count=data.affected_citizens_count,
            concerned_legal_texts=list(data.concerned_legal_texts),
            jurisprudence_refs=list(data.jurisprudence_refs),
            complexity_level=data.complexity_level,
            status="PUBLISHED",
        )
        self._session.add(problem)
        # Flush pour matérialiser l'``id`` unique attribué par la base (Exigence 2.6).
        await self._session.flush()
        return problem

    # ------------------------------------------------------------------ #
    # Rattachement et cardinalité des Réformes (Exigences 2.5, 4.1–4.4)   #
    # ------------------------------------------------------------------ #
    async def attach_reform(
        self,
        admin: User,
        problem_id: int,
        proposal_id: int,
        *,
        is_status_quo: bool,
    ) -> LegalProblemReform:
        """Rattache une Réforme_Proposée à un Problème_Juridique (Exigences 2.5, 4.1, 4.3).

        Réservé aux Administrateurs (Exigence 2.1) : toute autre tentative lève
        :class:`LegalReformPermissionError`. Le lien est créé dans la table
        d'association ``legal_problem_reforms`` **sans modifier** ``Proposal``
        (Exigence 13.1) ; ``is_status_quo`` marque, le cas échéant, l'Option_Statu_Quo
        (au plus une par Problème — Exigence 4.3).

        Lève :class:`LegalProblemNotFoundError` si le Problème_Juridique n'existe pas
        (⇒ 404).
        """
        self._authorize_admin(admin)
        await self._get_or_404(problem_id)

        link = LegalProblemReform(
            problem_id=problem_id,
            proposal_id=proposal_id,
            is_status_quo=is_status_quo,
        )
        self._session.add(link)
        # Flush pour matérialiser l'``id`` du lien attribué par la base.
        await self._session.flush()
        return link

    async def list_reforms(self, problem_id: int) -> list[ReformView]:
        """Liste les Réformes_Proposées rattachées, statu quo compris (Exigence 4.4).

        Lève :class:`LegalProblemNotFoundError` si le Problème_Juridique n'existe pas
        (⇒ 404). Chaque lien d'association est projeté en :class:`ReformView` en
        reprenant les attributs de la Proposition rattachée et le marqueur
        ``is_status_quo`` du lien (l'Option_Statu_Quo est ainsi signalée).
        """
        problem = await self._get_or_404(problem_id)
        return [
            ReformView(
                reform_id=link.proposal_id,
                problem_id=link.problem_id,
                proposal_id=link.proposal_id,
                slug=link.proposal.slug,
                title=link.proposal.title,
                status=link.proposal.status,
                is_status_quo=link.is_status_quo,
            )
            for link in problem.reform_links
        ]

    @staticmethod
    def _validate_cardinality(reforms: list[LegalProblemReform]) -> None:
        """Valide la cardinalité des Réformes rattachées (Exigences 4.1, 4.2, Property 3).

        La configuration est acceptée si et seulement si le nombre de Réformes
        appartient à l'intervalle ``[3, 5]`` **et** que le nombre de liens marqués
        ``is_status_quo`` vaut exactement 1. Toute autre configuration lève
        :class:`ReformCardinalityError` (⇒ 422), l'état du Problème_Juridique restant
        inchangé (Exigence 4.2).
        """
        count = len(reforms)
        status_quo_count = sum(1 for link in reforms if link.is_status_quo)
        if not (MIN_REFORMS <= count <= MAX_REFORMS) or status_quo_count != STATUS_QUO_COUNT:
            raise ReformCardinalityError(CARDINALITY_ERROR)

    # ------------------------------------------------------------------ #
    # Vote citoyen et décomptes (Exigences 8.1–8.4, 9, 13.2, 13.8)        #
    # ------------------------------------------------------------------ #
    async def cast_vote(
        self, user: User, problem_id: int, reform_id: int, value: int
    ) -> VoteCounts:
        """Enregistre le Vote_Citoyen d'un Utilisateur sur une Réforme (Exigences 8.1–8.4).

        La méthode **délègue** à ``vote_service`` (Exigence 13.8) : un UPSERT sur la
        contrainte ``UNIQUE(proposal_id, user_id)`` garantit l'unicité du Vote par
        Utilisateur et par Réforme (un vote existant voit sa ``value`` remplacée sans
        créer de ligne — Exigences 8.2, 13.2) ; ``value`` est borné à ``{+1, 0, -1}``
        par ``vote_service`` (Exigence 8.1). La Réforme doit être rattachée au
        Problème_Juridique : sinon :class:`LegalProblemNotFoundError` est levée (⇒ 404).

        Retourne les décomptes à jour exposés par « Réparer la loi », où
        ``participation_count = support_count + oppose_count`` (NEUTRE exclu —
        Exigences 9.1, 9.2).
        """
        await self._get_reform_link_or_404(problem_id, reform_id)
        await VoteService(self._session).cast(user, reform_id, value)
        return await self._reform_counts(reform_id)

    async def reform_vote_counts(self, problem_id: int, reform_id: int) -> VoteCounts:
        """Décomptes d'une Réforme_Proposée exposés par « Réparer la loi » (Exigence 9.1).

        Les décomptes sont calculés en **déléguant** à ``popularity_service``
        (Exigence 13.8) ; la couche « Réparer la loi » **expose explicitement**
        ``participation_count = support_count + oppose_count`` (les Votes NEUTRE sont
        exclus de ce décompte — Exigences 9.1, 9.2, Property 2), sans réimplémenter le
        calcul. Aucun tri ``ORDER BY vote_count`` n'est utilisé (Exigence 9.6) ; le
        score de ``popularity_service`` sert de clé lorsqu'un ordre est requis.

        La Réforme doit être rattachée au Problème_Juridique : sinon
        :class:`LegalProblemNotFoundError` est levée (⇒ 404).
        """
        await self._get_reform_link_or_404(problem_id, reform_id)
        return await self._reform_counts(reform_id)

    async def _reform_counts(self, reform_id: int) -> VoteCounts:
        """Projette la popularité déléguée en :class:`VoteCounts` de « Réparer la loi ».

        Reprend ``support_count``/``oppose_count`` de ``popularity_service`` et
        redéfinit ``participation_count = support_count + oppose_count`` (NEUTRE exclu —
        Exigence 9.1), sans réimplémenter l'agrégation des Votes (Exigence 13.8).
        """
        popularity = await PopularityService(self._session).compute(reform_id)
        support_count = popularity.support_count
        oppose_count = popularity.oppose_count
        return VoteCounts(
            support_count=support_count,
            oppose_count=oppose_count,
            participation_count=support_count + oppose_count,
        )

    # ------------------------------------------------------------------ #
    # Amendement (Exigences 8.7, 10) — délégué à proposal_service          #
    # ------------------------------------------------------------------ #
    async def propose_amendment(
        self, user: User, problem_id: int, reform_id: int, data: AmendmentInput
    ) -> ProposalVersion:
        """Enregistre un Amendement sur une Réforme_Proposée (Exigences 8.7, 10.1–10.3).

        La méthode **délègue** à ``proposal_service`` (Exigence 13.8) : la
        modification est appliquée via ``ProposalService.update``, qui crée une
        nouvelle :class:`ProposalVersion` complète (``snapshot``, ``change_summary``,
        ``edited_by``) et **incrémente** ``version`` d'exactement 1 sans jamais
        écraser une version antérieure (Exigences 10.1, 10.2, 10.3, 13.5, Property 7).
        Le texte de l'Amendement alimente la ``description`` de la Réforme.

        La Réforme doit être rattachée au Problème_Juridique : sinon
        :class:`LegalProblemNotFoundError` est levée (⇒ 404). Le contenu est borné à
        ``[1, 5000]`` caractères par :class:`AmendmentInput` (Exigence 8.9) ;
        l'autorisation (auteur ou Administrateur) est appliquée par
        ``proposal_service`` (Exigence 4.7).

        Retourne la :class:`ProposalVersion` nouvellement créée par l'Amendement.
        """
        await self._get_reform_link_or_404(problem_id, reform_id)

        proposal = await ProposalService(self._session).update(
            user,
            reform_id,
            ProposalUpdate(
                description=data.content,
                change_summary=data.change_summary or None,
            ),
        )
        # La version courante correspond à l'instantané créé par l'Amendement.
        version = await self._session.scalar(
            select(ProposalVersion).where(
                ProposalVersion.proposal_id == reform_id,
                ProposalVersion.version == proposal.version,
            )
        )
        # ``update`` vient de matérialiser cette version ; l'absence serait anormale.
        assert version is not None
        return version

    async def list_versions(self, problem_id: int, reform_id: int) -> list[ProposalVersion]:
        """Liste l'historique des Versions_De_Proposition d'une Réforme (Exigence 10.4).

        Les versions sont ordonnées par numéro de version **strictement croissant**
        (Property 8) ; l'historique est append-only et jamais écrasé (Exigence 13.5).

        La Réforme doit être rattachée au Problème_Juridique : sinon
        :class:`LegalProblemNotFoundError` est levée (⇒ 404).
        """
        await self._get_reform_link_or_404(problem_id, reform_id)
        result = await self._session.scalars(
            select(ProposalVersion)
            .where(ProposalVersion.proposal_id == reform_id)
            .order_by(ProposalVersion.version.asc())
        )
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Prise de position (Exigence 13.3) — délégué à argument_service       #
    # ------------------------------------------------------------------ #
    async def submit_position(
        self, user: User, problem_id: int, reform_id: int, position: str, content: str
    ) -> Argument:
        """Enregistre une prise de position ``FOR``/``AGAINST`` sur une Réforme (Exigence 13.3).

        La méthode **délègue** à ``argument_service`` (Exigence 13.8) : la
        ``position`` est contrainte à ``{FOR, AGAINST}`` par ``ArgumentService.create``
        (toute valeur hors ensemble lève ``InvalidArgumentPositionError`` ⇒ 422 —
        Exigence 13.4), et l'Argument est rattaché à la Réforme (``Proposal``).

        La Réforme doit être rattachée au Problème_Juridique : sinon
        :class:`LegalProblemNotFoundError` est levée (⇒ 404).
        """
        await self._get_reform_link_or_404(problem_id, reform_id)
        return await ArgumentService(self._session).create(user, reform_id, position, content)

    # ------------------------------------------------------------------ #
    # Signalement d'effet secondaire (Exigences 8.8, 8.9, 15) — modération #
    # ------------------------------------------------------------------ #
    async def report_side_effect(
        self, user: User, problem_id: int, reform_id: int, content: str
    ) -> SideEffectReport:
        """Crée un Signalement_D_Effet_Secondaire modéré sur une Réforme (Exigences 8.8, 8.9, 15.1).

        Le ``content`` est validé à ``[1, 5000]`` caractères après normalisation
        (Exigence 8.9, Property 6) : hors bornes ⇒ :class:`ContributionLengthError`
        (⇒ 422). Le Signalement est ensuite soumis à la modération en **déléguant** au
        Moteur_De_Modération (``moderation_service``) : « acceptable » ⇒ ``VISIBLE``,
        « douteux » ⇒ File_De_Modération (``PENDING``) sans publication (Exigence 15.1).
        Chaque décision de modération est consignée dans le Journal_D_Audit — id de
        la contribution, décision retenue, horodatage et origine automatique — via
        ``audit_service``, de façon cohérente avec le ``COMMENT_MODERATED`` du
        Moteur_De_Modération (Exigence 15.3).

        La Réforme doit être rattachée au Problème_Juridique : sinon
        :class:`LegalProblemNotFoundError` est levée (⇒ 404).
        """
        await self._get_reform_link_or_404(problem_id, reform_id)

        normalized = content.strip()
        if not (MIN_CONTRIBUTION_LENGTH <= len(normalized) <= MAX_CONTRIBUTION_LENGTH):
            raise ContributionLengthError(CONTRIBUTION_LENGTH_ERROR)

        # Classification déléguée au Moteur_De_Modération, sans réimplémenter le
        # filtre : le contenu est classé conforme/douteux puis mappé sur le statut
        # du Signalement (Exigence 15.1). Un Commentaire transitoire (non persisté)
        # sert de véhicule au classifieur partagé.
        probe = Comment(
            proposal_id=reform_id,
            author_id=user.id,
            content=normalized,
        )
        classification, reason = ModerationService(self._session).classify(probe.content)
        status = (
            _SIDE_EFFECT_STATUS_VISIBLE
            if classification is ModerationClassification.CONFORME
            else _SIDE_EFFECT_STATUS_PENDING
        )

        report = SideEffectReport(
            proposal_id=reform_id,
            author_id=user.id,
            content=normalized,
            status=status,
        )
        self._session.add(report)
        await self._session.flush()
        await self._session.refresh(report)

        # Consignation de la décision de modération dans le Journal_D_Audit
        # (Exigence 15.3) : id du Signalement, décision retenue (classification +
        # statut + publication), horodatage (porté par l'entrée d'audit) et origine
        # automatique. L'action réutilise ``COMMENT_MODERATED`` pour rester cohérente
        # avec la modération des Commentaires, en distinguant l'entité auditée.
        await AuditService(self._session).record(
            action=MODERATION_AUDIT_ACTION,
            entity_type=_SIDE_EFFECT_AUDIT_ENTITY_TYPE,
            entity_id=report.id,
            new_data={
                "status": status,
                "classification": classification.value,
                "published": classification is ModerationClassification.CONFORME,
                "reason": reason,
                "origin": _SIDE_EFFECT_AUDIT_ORIGIN,
            },
        )
        return report

    async def _get_reform_link_or_404(self, problem_id: int, reform_id: int) -> LegalProblemReform:
        """Charge le lien Réforme ↔ Problème ou lève :class:`LegalProblemNotFoundError`.

        Garantit que la Réforme (``reform_id`` ⇒ ``proposal_id``) est bien rattachée au
        Problème_Juridique ``problem_id`` avant tout vote ou décompte (⇒ 404 sinon).
        """
        link = await self._session.scalar(
            select(LegalProblemReform).where(
                LegalProblemReform.problem_id == problem_id,
                LegalProblemReform.proposal_id == reform_id,
            )
        )
        if link is None:
            raise LegalProblemNotFoundError(NOT_FOUND_ERROR)
        return link

    # ------------------------------------------------------------------ #
    # Utilitaires internes                                                #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _clamp_limit(limit: int) -> int:
        """Borne la taille de page à ``[1, 100]`` (Exigences 1.4, 14.1)."""
        if limit < 1:
            return 1
        return min(limit, MAX_PAGE_LIMIT)

    @staticmethod
    def _to_detail(problem: LegalProblem) -> LegalProblemDetail:
        """Projette un :class:`LegalProblem` (ORM) en :class:`LegalProblemDetail`.

        Chaque lien d'association ``reform_links`` est projeté en :class:`ReformView`
        en reprenant les attributs de la Proposition rattachée et le marqueur
        ``is_status_quo`` du lien (Exigence 4.4).
        """
        reforms = [
            ReformView(
                reform_id=link.proposal_id,
                problem_id=link.problem_id,
                proposal_id=link.proposal_id,
                slug=link.proposal.slug,
                title=link.proposal.title,
                status=link.proposal.status,
                is_status_quo=link.is_status_quo,
            )
            for link in problem.reform_links
        ]
        detail = LegalProblemDetail.model_validate(problem)
        detail.reforms = reforms
        return detail

    def _make_slug(self, title: str) -> str:
        """Dérive un slug ASCII, minuscule et tiretté à partir d'un titre (Exigence 2.6).

        Les accents sont retirés, les caractères non alphanumériques deviennent des
        tirets, et la longueur est bornée. Un titre vide (après nettoyage) retombe
        sur ``"probleme"``.
        """
        normalized = unicodedata.normalize("NFKD", title)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
        slug = slug[:_SLUG_MAX_LENGTH].strip("-")
        return slug or "probleme"

    async def _make_unique_slug(self, title: str) -> str:
        """Retourne un ``slug`` unique en base, en suffixant en cas de collision.

        ``slug`` est UNIQUE (Exigence 2.6) : si le slug de base est déjà pris, un
        suffixe aléatoire court est ajouté jusqu'à obtenir une valeur libre.
        """
        base = self._make_slug(title)
        candidate = base
        while await self._slug_exists(candidate):
            candidate = f"{base}-{secrets.token_hex(3)}"
        return candidate

    async def _slug_exists(self, slug: str) -> bool:
        """Indique si un ``slug`` est déjà utilisé par un Problème_Juridique."""
        existing = await self._session.scalar(
            select(LegalProblem.id).where(LegalProblem.slug == slug)
        )
        return existing is not None

    async def _get_or_404(self, problem_id: int) -> LegalProblem:
        """Charge un Problème_Juridique ou lève :class:`LegalProblemNotFoundError`."""
        problem = await self._session.get(LegalProblem, problem_id)
        if problem is None:
            raise LegalProblemNotFoundError(NOT_FOUND_ERROR)
        return problem

    @staticmethod
    def _authorize_admin(actor: User) -> None:
        """Autorise un Administrateur, refuse sinon (Exigence 2.1)."""
        if actor.is_admin:
            return
        raise LegalReformPermissionError(FORBIDDEN_ERROR)


# Le modèle d'association est importé pour garantir que la relation ``reform_links``
# est résoluble (référence différée dans ``LegalProblem``) lors de la lecture.
_ = LegalProblemReform
