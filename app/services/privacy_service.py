"""Service RGPD : export des données, suppression de compte, consentement
et politique de rétention (Exigences 28.4, 28.5).

Ce Service centralise les fonctions de conformité RGPD que la Plateforme doit
fournir (Exigence 28.4) :

* :meth:`export_user_data` — **export des données** : rassemble le compte et les
  contributions de l'Utilisateur (Propositions, Votes, Arguments, Commentaires)
  dans une structure portable. Le mot de passe n'est jamais exporté
  (Exigences 1.3, 1.8), et aucune donnée de profilage politique n'est produite
  (Exigences 28.1, 28.2) : seules des données déjà fournies/produites par
  l'Utilisateur sont restituées ;
* :meth:`get_consents` / :meth:`update_consents` — **gestion du consentement** :
  lecture et remplacement du registre de consentement fonctionnel de
  l'Utilisateur ;
* :meth:`delete_account` — **suppression de compte** : supprime les Votes,
  Arguments et Commentaires (dissociés par CASCADE côté base) et **anonymise** les
  Propositions rédigées (conservées au titre de l'intérêt collectif) en les
  réattribuant à un compte système, puis supprime le compte ;
* :meth:`retention_policy` — **politique de rétention explicite** (Exigence 28.5)
  pour les comptes, Votes, journaux, conversations IA et signalements.

La suppression est nécessaire car ``proposals.author_id`` porte une clé étrangère
``ON DELETE RESTRICT`` : supprimer directement un Utilisateur ayant rédigé des
Propositions échouerait. L'anonymisation réaffecte ces Propositions à un compte
système dédié afin de préserver le contenu public tout en effaçant le lien avec la
personne (droit à l'effacement pondéré par l'intérêt collectif).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.argument import Argument
from app.models.claim import Claim, ClaimComment, ClaimSupport, ContentReport
from app.models.comment import Comment
from app.models.consultation import Consultation, ConsultationParticipation
from app.models.national_scenario import NationalScenario, ScenarioShareLink
from app.models.proposal import Proposal, ProposalVersion
from app.models.user import User
from app.models.vote import Vote
from app.schemas.privacy import (
    AccountDeletionResult,
    ConsentState,
    DataExport,
    ExportedAccount,
    ExportedArgument,
    ExportedClaim,
    ExportedClaimComment,
    ExportedComment,
    ExportedConsultationParticipation,
    ExportedIncome,
    ExportedProposal,
    ExportedScenario,
    ExportedVote,
    RetentionPolicy,
    RetentionRule,
)
from app.services.user_income_service import UserIncomeService

# Compte système auquel sont réattribuées les Propositions anonymisées lors d'une
# suppression de compte. Son email réservé ne peut correspondre à aucun compte réel
# (le domaine ``.invalid`` est réservé par la RFC 2606).
_ANONYMIZED_AUTHOR_EMAIL: Final = "anonyme@partipolai.invalid"
_ANONYMIZED_AUTHOR_DISPLAY_NAME: Final = "Utilisateur supprimé"


class PrivacyError(Exception):
    """Erreur d'une opération RGPD (compte introuvable, etc.)."""


class PrivacyService:
    """Fonctions de conformité RGPD (export, consentement, suppression, rétention)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Export des données (Exigence 28.4)                                 #
    # ------------------------------------------------------------------ #
    async def export_user_data(self, user_id: int) -> DataExport:
        """Rassemble les données personnelles d'un Utilisateur en vue de l'export.

        Restitue le compte (sans ``password_hash``), ses Propositions, ses Votes,
        ses Arguments et ses Commentaires. Aucune donnée de profilage n'est
        produite (Exigences 28.1, 28.2).
        """
        user = await self._session.get(User, user_id)
        if user is None:
            raise PrivacyError("Compte introuvable.")

        proposals = (
            await self._session.scalars(
                select(Proposal).where(Proposal.author_id == user_id)
            )
        ).all()
        votes = (
            await self._session.scalars(
                select(Vote).where(Vote.user_id == user_id)
            )
        ).all()
        arguments = (
            await self._session.scalars(
                select(Argument).where(Argument.author_id == user_id)
            )
        ).all()
        comments = (
            await self._session.scalars(
                select(Comment).where(Comment.author_id == user_id)
            )
        ).all()
        scenarios = (
            await self._session.scalars(
                select(NationalScenario).where(NationalScenario.user_id == user_id)
            )
        ).all()

        claims = (
            await self._session.scalars(
                select(Claim).where(Claim.author_id == user_id)
            )
        ).all()
        claim_comments = (
            await self._session.scalars(
                select(ClaimComment).where(ClaimComment.author_id == user_id)
            )
        ).all()

        # Participations aux consultations : titre et date seulement, jamais le
        # contenu des réponses (Exigence 10.11).
        participations: list[ExportedConsultationParticipation] = []
        participation_rows = (
            await self._session.scalars(
                select(ConsultationParticipation).where(
                    ConsultationParticipation.user_id == user_id
                )
            )
        ).all()
        for participation in sorted(
            participation_rows, key=lambda p: p.participated_on, reverse=True
        ):
            consultation = await self._session.get(
                Consultation, participation.consultation_id
            )
            if consultation is None:
                continue
            participations.append(
                ExportedConsultationParticipation(
                    consultation_id=participation.consultation_id,
                    title=consultation.title,
                    participated_on=participation.participated_on,
                )
            )

        income_row = await UserIncomeService(self._session).get(user_id)

        return DataExport(
            generated_at=datetime.now(UTC),
            income=(
                ExportedIncome(
                    statut=income_row.statut,
                    revenu_net=income_row.revenu_net,
                    updated_at=income_row.updated_at,
                )
                if income_row is not None
                else None
            ),
            account=ExportedAccount.model_validate(user),
            proposals=[
                ExportedProposal(
                    id=p.id,
                    slug=p.slug,
                    title=p.title,
                    status=p.status,
                    version=p.version,
                    created_at=p.created_at,
                )
                for p in proposals
            ],
            votes=[
                ExportedVote(
                    proposal_id=v.proposal_id,
                    value=v.value,
                    created_at=v.created_at,
                )
                for v in votes
            ],
            arguments=[
                ExportedArgument(
                    id=a.id,
                    proposal_id=a.proposal_id,
                    position=a.position,
                    content=a.content,
                    created_at=a.created_at,
                )
                for a in arguments
            ],
            comments=[
                ExportedComment(
                    id=c.id,
                    proposal_id=c.proposal_id,
                    parent_id=c.parent_id,
                    content=c.content,
                    status=c.status,
                    created_at=c.created_at,
                )
                for c in comments
            ],
            saved_scenarios=[
                ExportedScenario(
                    id=s.id,
                    name=s.name,
                    parameters=dict(s.parameters or {}),
                    model_version=s.model_version,
                    dataset_version=s.dataset_version,
                    created_at=s.created_at,
                )
                for s in scenarios
            ],
            claims=[
                ExportedClaim(
                    id=c.id,
                    title=c.title,
                    body=c.body,
                    status=c.status,
                    category_id=c.category_id,
                    published_at=c.published_at,
                    created_at=c.created_at,
                )
                for c in claims
            ],
            claim_comments=[
                ExportedClaimComment(
                    id=c.id,
                    claim_id=c.claim_id,
                    body=c.body,
                    status=c.status,
                    created_at=c.created_at,
                )
                for c in claim_comments
            ],
            consultation_participations=participations,
        )

    # ------------------------------------------------------------------ #
    # Gestion du consentement (Exigence 28.4)                            #
    # ------------------------------------------------------------------ #
    async def get_consents(self, user_id: int) -> ConsentState:
        """Retourne l'état courant du consentement de l'Utilisateur."""
        user = await self._session.get(User, user_id)
        if user is None:
            raise PrivacyError("Compte introuvable.")
        return ConsentState(consents=dict(user.consents or {}))

    async def update_consents(
        self, user_id: int, consents: dict[str, bool]
    ) -> ConsentState:
        """Remplace le registre de consentement de l'Utilisateur (Exigence 28.4).

        Seuls des choix de consentement fonctionnels (``{clé: bool}``) sont
        conservés ; aucune donnée de profilage n'est acceptée (Exigences 28.1,
        28.2).
        """
        user = await self._session.get(User, user_id)
        if user is None:
            raise PrivacyError("Compte introuvable.")
        # Normalisation défensive : ne conserver que des valeurs booléennes.
        normalized = {str(key): bool(value) for key, value in consents.items()}
        user.consents = normalized
        await self._session.flush()
        return ConsentState(consents=normalized)

    # ------------------------------------------------------------------ #
    # Suppression de compte (Exigence 28.4)                              #
    # ------------------------------------------------------------------ #
    async def delete_account(self, user_id: int) -> AccountDeletionResult:
        """Supprime le compte et ses contributions personnelles (Exigence 28.4).

        Les Votes, Arguments et Commentaires sont supprimés. Les Propositions
        rédigées sont **anonymisées** (réattribuées à un compte système) plutôt
        que supprimées, afin de préserver le contenu public tout en effaçant le
        lien avec la personne. Le compte est ensuite supprimé.
        """
        user = await self._session.get(User, user_id)
        if user is None:
            raise PrivacyError("Compte introuvable.")

        # Décomptes avant suppression (pour le récapitulatif).
        deleted_votes = await self._count(Vote, Vote.user_id, user_id)
        deleted_arguments = await self._count(Argument, Argument.author_id, user_id)
        deleted_comments = await self._count(Comment, Comment.author_id, user_id)
        proposals_to_anonymize = await self._count(
            Proposal, Proposal.author_id, user_id
        )
        scenarios = (
            await self._session.scalars(
                select(NationalScenario).where(NationalScenario.user_id == user_id)
            )
        ).all()
        deleted_scenarios = len(scenarios)

        # Scénarios enregistrés : liens de partage d'abord, puis scénarios
        # (Exigence 2.12 ; le CASCADE côté base est une seconde garantie).
        for scenario in scenarios:
            await self._session.execute(
                delete(ScenarioShareLink).where(
                    ScenarioShareLink.scenario_id == scenario.id
                )
            )
        if scenarios:
            await self._session.execute(
                delete(NationalScenario).where(NationalScenario.user_id == user_id)
            )

        # Revendications : soutiens, signalements, commentaires puis revendications
        # (Exigence 6.8).
        (
            deleted_claims,
            deleted_claim_comments,
            deleted_claim_supports,
            deleted_reports,
        ) = await self._delete_claim_data(user_id)

        # Participations aux consultations (Exigence 10.12) : seules les lignes de
        # participation sont supprimées ; les réponses, non reliées au compte,
        # restent en base de façon anonyme.
        deleted_participations = await self._count(
            ConsultationParticipation, ConsultationParticipation.user_id, user_id
        )
        await self._session.execute(
            delete(ConsultationParticipation).where(
                ConsultationParticipation.user_id == user_id
            )
        )

        # Supprime explicitement les contributions personnelles. (Le CASCADE côté
        # base couvre aussi ces cas ; la suppression explicite rend l'intention
        # claire et le décompte fiable quel que soit le backend.)
        await self._session.execute(delete(Vote).where(Vote.user_id == user_id))
        await self._session.execute(
            delete(Argument).where(Argument.author_id == user_id)
        )
        await self._session.execute(
            delete(Comment).where(Comment.author_id == user_id)
        )

        # Anonymise les Propositions rédigées : réaffectation à un compte système.
        anonymized_proposals = 0
        if proposals_to_anonymize:
            system_author = await self._get_or_create_anonymized_author()
            await self._session.execute(
                update(Proposal)
                .where(Proposal.author_id == user_id)
                .values(author_id=system_author.id)
            )
            # Dissocie aussi l'éditeur des versions historiques (colonne SET NULL).
            await self._session.execute(
                update(ProposalVersion)
                .where(ProposalVersion.edited_by == user_id)
                .values(edited_by=None)
            )
            anonymized_proposals = proposals_to_anonymize

        # Supprime enfin le compte lui-même.
        await self._session.execute(delete(User).where(User.id == user_id))
        await self._session.flush()

        return AccountDeletionResult(
            deleted=True,
            anonymized_proposals=anonymized_proposals,
            deleted_votes=deleted_votes,
            deleted_arguments=deleted_arguments,
            deleted_comments=deleted_comments,
            deleted_scenarios=deleted_scenarios,
            deleted_claims=deleted_claims,
            deleted_claim_comments=deleted_claim_comments,
            deleted_claim_supports=deleted_claim_supports,
            deleted_content_reports=deleted_reports,
            deleted_consultation_participations=deleted_participations,
        )

    async def _delete_claim_data(self, user_id: int) -> tuple[int, int, int, int]:
        """Supprime soutiens, signalements, revendications et commentaires (Exigence 6.8).

        Retourne ``(revendications, commentaires, soutiens, signalements)``
        supprimés. Les compteurs dénormalisés des contenus d'autrui sont ajustés
        pour les soutiens et signalements actifs retirés.
        """
        session = self._session

        # 1. Soutiens émis par l'utilisateur (compteur des revendications ajusté).
        supports = (
            await session.scalars(
                select(ClaimSupport).where(ClaimSupport.user_id == user_id)
            )
        ).all()
        for support in supports:
            claim = await session.get(Claim, support.claim_id)
            if claim is not None and claim.support_count > 0:
                claim.support_count -= 1
        if supports:
            await session.execute(
                delete(ClaimSupport).where(ClaimSupport.user_id == user_id)
            )
        deleted_supports = len(supports)

        # 2. Signalements émis par l'utilisateur (compteurs ajustés si actifs).
        reports = (
            await session.scalars(
                select(ContentReport).where(ContentReport.reporter_id == user_id)
            )
        ).all()
        for report in reports:
            if not report.is_active:
                continue
            if report.claim_id is not None:
                target = await session.get(Claim, report.claim_id)
                if target is not None and target.report_count > 0:
                    target.report_count -= 1
            if report.comment_id is not None:
                target_comment = await session.get(ClaimComment, report.comment_id)
                if target_comment is not None and target_comment.report_count > 0:
                    target_comment.report_count -= 1
        if reports:
            await session.execute(
                delete(ContentReport).where(ContentReport.reporter_id == user_id)
            )
        deleted_reports = len(reports)

        # 3. Commentaires de l'utilisateur : signalements reçus puis commentaires.
        own_comments = (
            await session.scalars(
                select(ClaimComment).where(ClaimComment.author_id == user_id)
            )
        ).all()
        for comment in own_comments:
            deleted_reports += await self._delete_reports_on_comment(comment.id)
        if own_comments:
            await session.execute(
                delete(ClaimComment).where(ClaimComment.author_id == user_id)
            )
        deleted_comments = len(own_comments)

        # 4. Revendications de l'utilisateur : tout ce qui s'y rattache, puis elles.
        own_claims = (
            await session.scalars(select(Claim).where(Claim.author_id == user_id))
        ).all()
        for claim in own_claims:
            claim_comments = (
                await session.scalars(
                    select(ClaimComment).where(ClaimComment.claim_id == claim.id)
                )
            ).all()
            for comment in claim_comments:
                deleted_reports += await self._delete_reports_on_comment(comment.id)
            if claim_comments:
                await session.execute(
                    delete(ClaimComment).where(ClaimComment.claim_id == claim.id)
                )
            deleted_comments += len(claim_comments)

            claim_reports = (
                await session.scalars(
                    select(ContentReport).where(ContentReport.claim_id == claim.id)
                )
            ).all()
            if claim_reports:
                await session.execute(
                    delete(ContentReport).where(ContentReport.claim_id == claim.id)
                )
            deleted_reports += len(claim_reports)

            claim_supports = (
                await session.scalars(
                    select(ClaimSupport).where(ClaimSupport.claim_id == claim.id)
                )
            ).all()
            if claim_supports:
                await session.execute(
                    delete(ClaimSupport).where(ClaimSupport.claim_id == claim.id)
                )
            deleted_supports += len(claim_supports)
        if own_claims:
            await session.execute(delete(Claim).where(Claim.author_id == user_id))

        return len(own_claims), deleted_comments, deleted_supports, deleted_reports

    async def _delete_reports_on_comment(self, comment_id: int) -> int:
        """Supprime les signalements portant sur un commentaire ; retourne leur nombre."""
        reports = (
            await self._session.scalars(
                select(ContentReport).where(ContentReport.comment_id == comment_id)
            )
        ).all()
        if reports:
            await self._session.execute(
                delete(ContentReport).where(ContentReport.comment_id == comment_id)
            )
        return len(reports)

    async def _get_or_create_anonymized_author(self) -> User:
        """Retourne (ou crée) le compte système d'anonymisation des Propositions."""
        existing = await self._session.scalar(
            select(User).where(User.email == _ANONYMIZED_AUTHOR_EMAIL)
        )
        if existing is not None:
            return existing
        system_author = User(
            email=_ANONYMIZED_AUTHOR_EMAIL,
            # Mot de passe inutilisable : le compte système ne se connecte jamais.
            password_hash="!",
            display_name=_ANONYMIZED_AUTHOR_DISPLAY_NAME,
            is_active=False,
            is_verified=False,
            is_admin=False,
            consents={},
        )
        self._session.add(system_author)
        await self._session.flush()
        return system_author

    async def _count(self, model: type, column: Any, user_id: int) -> int:
        """Compte les lignes de ``model`` dont ``column == user_id``."""
        rows: Sequence[Any] = (
            await self._session.scalars(select(model).where(column == user_id))
        ).all()
        return len(rows)

    # ------------------------------------------------------------------ #
    # Politique de rétention explicite (Exigence 28.5)                   #
    # ------------------------------------------------------------------ #
    @staticmethod
    def retention_policy() -> RetentionPolicy:
        """Retourne la politique de rétention explicite de la Plateforme.

        Couvre les comptes, Votes, journaux, conversations IA et signalements
        (Exigence 28.5). Les durées sont exprimées en clair afin d'être exposées
        aux Utilisateurs sur la page ``/confidentialite``.
        """
        return RetentionPolicy(
            rules=[
                RetentionRule(
                    category="Comptes",
                    retention=(
                        "Conservés tant que le compte est actif ; supprimés sur "
                        "demande de l'Utilisateur (suppression de compte) et après "
                        "24 mois d'inactivité."
                    ),
                    basis="Exécution du service et consentement (Exigences 28.4, 28.5).",
                ),
                RetentionRule(
                    category="Votes",
                    retention=(
                        "Conservés de manière agrégée ; les Votes individuels sont "
                        "supprimés à la suppression du compte de leur auteur."
                    ),
                    basis="Intérêt collectif de la Plateforme, données anonymisées (Exigence 22).",
                ),
                RetentionRule(
                    category="Journaux (logs, audit)",
                    retention=(
                        "Conservés au maximum 12 mois ; l'adresse IP n'est jamais "
                        "conservée en clair (seul un ip_hash est stocké)."
                    ),
                    basis="Sécurité et traçabilité (Exigences 29.2, 30.1).",
                ),
                RetentionRule(
                    category="Conversations IA",
                    retention=(
                        "Le contenu des échanges n'est pas conservé ; seules des "
                        "métadonnées techniques (request_id, modèle, documents "
                        "récupérés) sont journalisées au maximum 3 mois."
                    ),
                    basis="Observabilité et amélioration du service (Exigences 30.2, 30.3).",
                ),
                RetentionRule(
                    category="Signalements",
                    retention=(
                        "Conservés le temps du traitement de la modération, puis "
                        "au maximum 6 mois après clôture."
                    ),
                    basis="Modération et sécurité de la Plateforme (Exigence 17).",
                ),
                RetentionRule(
                    category="Engagements citoyens (déclaratifs)",
                    retention=(
                        "Conservés tant que le compte existe ; supprimés avec le compte. "
                        "Ils sont déclaratifs et non vérifiés ; aucune transaction ni "
                        "donnée bancaire n'est collectée."
                    ),
                    basis="Consentement explicite à l'agrégation, retirable à tout moment.",
                ),
                RetentionRule(
                    category="Simulations enregistrées",
                    retention=(
                        "Seules les données saisies (pourcentages de réduction, nombre "
                        "d'adultes) sont conservées ; supprimées avec le compte. Les "
                        "simulations anonymes ne sont liées à aucune personne."
                    ),
                    basis="Reproductibilité des résultats par version de modèle.",
                ),
                RetentionRule(
                    category="Statut et revenu net saisis",
                    retention=(
                        "Seuls le dernier statut et le dernier revenu net annuel saisis "
                        "sont conservés pour pré-remplir le formulaire ; aucun impôt, "
                        "aide ni donnée immobilière n'est stocké. Supprimés avec le "
                        "compte et inclus dans l'export."
                    ),
                    basis="Service demandé par l'Utilisateur connecté, minimisation des données.",
                ),
                RetentionRule(
                    category="Agrégats nationaux",
                    retention=(
                        "Totaux anonymes conservés sans limite ; ils ne contiennent aucune "
                        "donnée individuelle et ne sont publiés qu'au-delà d'un seuil "
                        "minimal de participants."
                    ),
                    basis="Intérêt collectif, données anonymisées.",
                ),
            ]
        )
