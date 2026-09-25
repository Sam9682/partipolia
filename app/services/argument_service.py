"""Service des Arguments (Exigence 8).

Ce Service implémente le contrat ``ArgumentService`` de la conception :

* ``create`` — enregistre un Argument prenant une ``position`` parmi
  ``{FOR, AGAINST}`` sur une Proposition existante (Exigence 8.1) ;
* ``get`` — renvoie un Argument par identifiant (lecture, Exigence 8.2) ;
* ``list_for_proposal`` — liste les Arguments rattachés à une Proposition
  (lecture, Exigence 8.2) ;
* ``update`` — modifie le ``content`` d'un Argument (Exigence 8.2) ;
* ``delete`` — supprime un Argument (Exigence 8.2).

Contrôle d'accès (Exigence 8.3) : seuls l'auteur de l'Argument ou un
Administrateur peuvent le modifier (``update``) ou le supprimer (``delete``) ;
toute autre tentative lève :class:`ArgumentPermissionError`.

Le Service est instancié par requête avec une ``AsyncSession`` (SQLAlchemy 2.x
asynchrone), avec chargement anticipé ``selectin`` sur les relations (défini au
niveau du modèle) pour éviter le N+1.
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.argument import ARGUMENT_POSITIONS, Argument
from app.models.proposal import Proposal
from app.models.user import User

# Messages d'erreur génériques (Exigences 31.1, 31.2).
ARGUMENT_NOT_FOUND_ERROR: Final = "Argument introuvable."
PROPOSAL_NOT_FOUND_ERROR: Final = "Proposition introuvable."
INVALID_POSITION_ERROR: Final = "La position doit être FOR ou AGAINST."
FORBIDDEN_ERROR: Final = "Action réservée à l'auteur ou à un administrateur."


class ArgumentError(Exception):
    """Erreur générique du Service des Arguments."""


class InvalidArgumentPositionError(ArgumentError):
    """La ``position`` fournie n'appartient pas à ``{FOR, AGAINST}`` (Exigence 8.1)."""


class ArgumentNotFoundError(ArgumentError):
    """L'Argument demandé n'existe pas (⇒ 404, Exigences 8.2, 31.2)."""


class ProposalNotFoundError(ArgumentError):
    """La Proposition référencée n'existe pas (⇒ 404, Exigence 31.2)."""


class ArgumentPermissionError(ArgumentError):
    """Action réservée à l'auteur ou à un Administrateur (⇒ 403, Exigence 8.3)."""


class ArgumentService:
    """Logique des Arguments : création, lecture, modification, suppression (Exigence 8).

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Création (Exigence 8.1)                                             #
    # ------------------------------------------------------------------ #
    async def create(
        self, author: User, proposal_id: int, position: str, content: str
    ) -> Argument:
        """Crée un Argument ``FOR``/``AGAINST`` sur une Proposition (Exigence 8.1).

        L'existence de la Proposition est vérifiée (sinon
        :class:`ProposalNotFoundError`) et la ``position`` est contrainte à
        ``{FOR, AGAINST}`` (sinon :class:`InvalidArgumentPositionError`).
        """
        if position not in ARGUMENT_POSITIONS:
            raise InvalidArgumentPositionError(INVALID_POSITION_ERROR)

        proposal = await self._session.get(Proposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(PROPOSAL_NOT_FOUND_ERROR)

        argument = Argument(
            proposal_id=proposal_id,
            author_id=author.id,
            position=position,
            content=content,
        )
        self._session.add(argument)
        await self._session.flush()
        await self._session.refresh(argument)
        return argument

    # ------------------------------------------------------------------ #
    # Lecture (Exigence 8.2)                                              #
    # ------------------------------------------------------------------ #
    async def get(self, argument_id: int) -> Argument:
        """Retourne un Argument par identifiant, sinon ``404`` (Exigence 8.2)."""
        return await self._get_or_404(argument_id)

    async def list_for_proposal(self, proposal_id: int) -> list[Argument]:
        """Liste les Arguments rattachés à une Proposition (Exigence 8.2).

        Les Arguments sont ordonnés par identifiant croissant (ordre
        chronologique de création, historique append-only) pour un ordre
        déterministe.
        """
        result = await self._session.scalars(
            select(Argument)
            .where(Argument.proposal_id == proposal_id)
            .order_by(Argument.id.asc())
        )
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Modification (Exigences 8.2, 8.3)                                   #
    # ------------------------------------------------------------------ #
    async def update(self, actor: User, argument_id: int, content: str) -> Argument:
        """Modifie le ``content`` d'un Argument (Exigence 8.2).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent modifier
        (Exigence 8.3) ; toute autre tentative lève
        :class:`ArgumentPermissionError`.
        """
        argument = await self._get_or_404(argument_id)
        self._authorize(actor, argument)

        argument.content = content
        await self._session.flush()
        await self._session.refresh(argument)
        return argument

    # ------------------------------------------------------------------ #
    # Suppression (Exigences 8.2, 8.3)                                    #
    # ------------------------------------------------------------------ #
    async def delete(self, actor: User, argument_id: int) -> None:
        """Supprime un Argument (Exigence 8.2).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent supprimer
        (Exigence 8.3) ; toute autre tentative lève
        :class:`ArgumentPermissionError`.
        """
        argument = await self._get_or_404(argument_id)
        self._authorize(actor, argument)

        await self._session.delete(argument)
        await self._session.flush()

    # ------------------------------------------------------------------ #
    # Utilitaires internes                                                #
    # ------------------------------------------------------------------ #
    async def _get_or_404(self, argument_id: int) -> Argument:
        """Charge un Argument ou lève :class:`ArgumentNotFoundError`."""
        argument = await self._session.get(Argument, argument_id)
        if argument is None:
            raise ArgumentNotFoundError(ARGUMENT_NOT_FOUND_ERROR)
        return argument

    @staticmethod
    def _authorize(actor: User, argument: Argument) -> None:
        """Autorise l'auteur ou un Administrateur, refuse sinon (Exigence 8.3)."""
        if actor.is_admin or actor.id == argument.author_id:
            return
        raise ArgumentPermissionError(FORBIDDEN_ERROR)
