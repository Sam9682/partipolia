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

from datetime import datetime, timezone
from typing import Final

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.argument import Argument
from app.models.comment import Comment
from app.models.proposal import Proposal, ProposalVersion
from app.models.user import User
from app.models.vote import Vote
from app.schemas.privacy import (
    AccountDeletionResult,
    ConsentState,
    DataExport,
    ExportedAccount,
    ExportedArgument,
    ExportedComment,
    ExportedProposal,
    ExportedVote,
    RetentionPolicy,
    RetentionRule,
)

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

        return DataExport(
            generated_at=datetime.now(timezone.utc),
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
        )

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

    async def _count(self, model: type, column: object, user_id: int) -> int:
        """Compte les lignes de ``model`` dont ``column == user_id``."""
        rows = (
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
            ]
        )
