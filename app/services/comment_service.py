"""Service des Commentaires (Exigence 9).

Ce Service implémente le contrat ``CommentService`` de la conception :

* ``create`` — publie un Commentaire sur une Proposition, éventuellement en
  réponse à un Commentaire parent (fil de discussion, Exigence 9.2), puis le
  soumet **systématiquement** au Moteur_De_Modération (Exigences 9.1, 17.x). Un
  Commentaire conforme est enregistré ``VISIBLE`` (Exigence 9.4), un Commentaire
  douteux est placé en File_De_Modération (``PENDING``) sans publication. La
  méthode renvoie une :class:`CommentSubmission` décrivant le Commentaire et la
  décision de modération ;
* ``list_thread`` — reconstitue les fils de discussion d'une Proposition sous
  forme d'arborescence de :class:`CommentNode` via ``parent_id`` (Exigence 9.2) ;
* ``update`` — modifie le ``content`` d'un Commentaire (Exigence 9.3) ;
* ``delete`` — supprime un Commentaire (Exigence 9.3).

Contrôle d'accès (Exigences 9.3, 4.7) : seuls l'auteur du Commentaire ou un
Administrateur peuvent le modifier (``update``) ou le supprimer (``delete``) ;
toute autre tentative lève :class:`CommentPermissionError`.

Le Service est instancié par requête avec une ``AsyncSession`` et délègue la
modération au :class:`~app.services.moderation_service.ModerationService` adossé
à la même session. Comme les autres Services, il ``flush`` sans ``commit`` (la
transaction est gérée au niveau de la requête).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.models.proposal import Proposal
from app.schemas.auth import UserPublic
from app.schemas.comment import CommentNode, CommentPublic, CommentSubmission
from app.services.moderation_service import ModerationService

# Messages d'erreur génériques (Exigences 31.1, 31.2).
COMMENT_NOT_FOUND_ERROR: Final = "Commentaire introuvable."
PROPOSAL_NOT_FOUND_ERROR: Final = "Proposition introuvable."
PARENT_NOT_FOUND_ERROR: Final = "Commentaire parent introuvable."
FORBIDDEN_ERROR: Final = "Action réservée à l'auteur ou à un administrateur."


class CommentError(Exception):
    """Erreur générique du Service des Commentaires."""


class CommentNotFoundError(CommentError):
    """Le Commentaire demandé n'existe pas (⇒ 404, Exigences 9.3, 31.2)."""


class ProposalNotFoundError(CommentError):
    """La Proposition référencée n'existe pas (⇒ 404, Exigence 31.2)."""


class ParentCommentNotFoundError(CommentError):
    """Le Commentaire parent référencé n'existe pas (⇒ 404, Exigences 9.2, 31.2)."""


class CommentPermissionError(CommentError):
    """Action réservée à l'auteur ou à un Administrateur (⇒ 403, Exigence 9.3)."""


class CommentService:
    """Logique des Commentaires : création, fils, modification, suppression (Exigence 9).

    Le Service est instancié par requête avec une ``AsyncSession``. Il délègue la
    modération de chaque Commentaire créé au :class:`ModerationService` (Exigences
    9.1, 17.x).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._moderation = ModerationService(session)

    # ------------------------------------------------------------------ #
    # Création + modération (Exigences 9.1, 9.2, 9.4, 17.x)              #
    # ------------------------------------------------------------------ #
    async def create(
        self,
        user: UserPublic,
        proposal_id: int,
        content: str,
        parent_id: int | None = None,
        *,
        ip_hash: str | None = None,
    ) -> CommentSubmission:
        """Publie un Commentaire puis le soumet au Moteur_De_Modération.

        La Proposition doit exister (sinon :class:`ProposalNotFoundError`). Si un
        ``parent_id`` est fourni, le Commentaire parent doit exister et appartenir
        à la même Proposition (sinon :class:`ParentCommentNotFoundError`, Exigence
        9.2).

        Le Commentaire est créé puis **systématiquement** soumis au
        Moteur_De_Modération (Exigences 9.1, 17.1) : conforme ⇒ ``VISIBLE``
        (Exigence 9.4), douteux ⇒ File_De_Modération (``PENDING``) sans
        publication (Exigence 17.3). ``ip_hash`` est transmis tel quel au
        Journal_D_Audit par la modération (jamais l'IP en clair, Exigence 29.2).

        Retourne une :class:`CommentSubmission` (Commentaire + décision).
        """
        proposal = await self._session.get(Proposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(PROPOSAL_NOT_FOUND_ERROR)

        if parent_id is not None:
            parent = await self._session.get(Comment, parent_id)
            if parent is None or parent.proposal_id != proposal_id:
                raise ParentCommentNotFoundError(PARENT_NOT_FOUND_ERROR)

        comment = Comment(
            proposal_id=proposal_id,
            author_id=user.id,
            parent_id=parent_id,
            content=content,
        )
        self._session.add(comment)
        await self._session.flush()

        # Passage systématique par le Moteur_De_Modération (Exigences 9.1, 17.x) :
        # il fixe le statut résultant (VISIBLE/PENDING) et consigne la décision.
        decision = await self._moderation.moderate(comment, ip_hash=ip_hash)
        await self._session.refresh(comment)

        return CommentSubmission(
            comment=CommentPublic.model_validate(comment),
            moderation=decision,
        )

    # ------------------------------------------------------------------ #
    # Lecture : fils de discussion (Exigence 9.2)                        #
    # ------------------------------------------------------------------ #
    async def list_thread(self, proposal_id: int) -> list[CommentNode]:
        """Reconstitue les fils de discussion d'une Proposition (Exigence 9.2).

        Tous les Commentaires de la Proposition sont chargés puis assemblés en
        arborescence à partir de ``parent_id`` : les Commentaires racines
        (``parent_id is None``) portent leurs ``replies``, elles-mêmes récursives.
        L'ordre est chronologique (identifiant croissant) à chaque niveau, pour un
        rendu déterministe.
        """
        result = await self._session.scalars(
            select(Comment)
            .where(Comment.proposal_id == proposal_id)
            .order_by(Comment.id.asc())
        )
        comments = list(result.all())

        # Construit les nœuds (sans réponses) indexés par identifiant, en
        # préservant l'ordre chronologique de chargement.
        nodes: dict[int, CommentNode] = {
            comment.id: CommentNode(
                id=comment.id,
                proposal_id=comment.proposal_id,
                author_id=comment.author_id,
                parent_id=comment.parent_id,
                content=comment.content,
                status=comment.status,
                created_at=comment.created_at,
                replies=[],
            )
            for comment in comments
        }

        # Rattache chaque nœud à son parent ; les nœuds sans parent (ou dont le
        # parent est absent de la Proposition) forment les racines du fil.
        roots: list[CommentNode] = []
        for comment in comments:
            node = nodes[comment.id]
            parent_id = comment.parent_id
            if parent_id is not None and parent_id in nodes:
                nodes[parent_id].replies.append(node)
            else:
                roots.append(node)
        return roots

    # ------------------------------------------------------------------ #
    # Modification (Exigences 9.3, 4.7)                                  #
    # ------------------------------------------------------------------ #
    async def update(self, actor: UserPublic, comment_id: int, content: str) -> Comment:
        """Modifie le ``content`` d'un Commentaire (Exigence 9.3).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent modifier
        (Exigences 9.3, 4.7) ; toute autre tentative lève
        :class:`CommentPermissionError`.
        """
        comment = await self._get_or_404(comment_id)
        self._authorize(actor, comment)

        comment.content = content
        await self._session.flush()
        await self._session.refresh(comment)
        return comment

    # ------------------------------------------------------------------ #
    # Suppression (Exigences 9.3, 4.7)                                   #
    # ------------------------------------------------------------------ #
    async def delete(self, actor: UserPublic, comment_id: int) -> None:
        """Supprime un Commentaire (Exigence 9.3).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent supprimer
        (Exigences 9.3, 4.7) ; toute autre tentative lève
        :class:`CommentPermissionError`.
        """
        comment = await self._get_or_404(comment_id)
        self._authorize(actor, comment)

        await self._session.delete(comment)
        await self._session.flush()

    # ------------------------------------------------------------------ #
    # Utilitaires internes                                                #
    # ------------------------------------------------------------------ #
    async def _get_or_404(self, comment_id: int) -> Comment:
        """Charge un Commentaire ou lève :class:`CommentNotFoundError`."""
        comment = await self._session.get(Comment, comment_id)
        if comment is None:
            raise CommentNotFoundError(COMMENT_NOT_FOUND_ERROR)
        return comment

    @staticmethod
    def _authorize(actor: UserPublic, comment: Comment) -> None:
        """Autorise l'auteur ou un Administrateur, refuse sinon (Exigence 9.3)."""
        if actor.is_admin or actor.id == comment.author_id:
            return
        raise CommentPermissionError(FORBIDDEN_ERROR)
