"""Service des Sources documentaires (Exigence 10).

Ce Service implémente le contrat ``SourceService`` de la conception :

* ``create`` — crée une Source (réservé à l'Administrateur, Exigence 10.4) à
  partir des champs ``title``, ``url``, ``publisher``, ``source_type``,
  ``publication_date`` et ``is_verified`` (Exigence 10.1) ; ``source_type`` est
  contraint aux 7 valeurs autorisées (Exigence 10.2) ;
* ``attach_to_proposal`` — rattache une Source existante à une Proposition en
  enregistrant l'association ``proposal_sources`` avec un ``relevance_score``
  (réservé à l'Administrateur, Exigences 10.3, 10.4) ;
* ``list_sources`` — liste publique des Sources, filtrable par ``source_type``
  (Exigence 10.1).

La gestion des Sources (création et rattachement) est réservée à l'Administrateur
au niveau de la couche API (:func:`app.api.deps.require_admin`, Exigence 10.4) ;
ce Service valide en complément que le ``source_type`` fourni appartient bien à
l'ensemble autorisé (Exigence 10.2) et que les entités référencées existent.

Le Service est instancié par requête avec une ``AsyncSession`` (SQLAlchemy 2.x
asynchrone).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposal import Proposal
from app.models.source import SOURCE_TYPES, ProposalSource, Source
from app.schemas.source import ProposalSourceAttach, SourceCreate

# Messages d'erreur génériques (Exigences 31.1, 31.2).
SOURCE_NOT_FOUND_ERROR: Final = "Source introuvable."
PROPOSAL_NOT_FOUND_ERROR: Final = "Proposition introuvable."
INVALID_SOURCE_TYPE_ERROR: Final = "Type de source invalide."


class SourceError(Exception):
    """Erreur générique du Service des Sources."""


class InvalidSourceTypeError(SourceError):
    """Le ``source_type`` fourni n'appartient pas aux 7 valeurs autorisées (Exigence 10.2)."""


class SourceNotFoundError(SourceError):
    """La Source référencée n'existe pas (Exigences 10.3, 31.2)."""


class ProposalNotFoundError(SourceError):
    """La Proposition référencée n'existe pas (Exigences 10.3, 31.2)."""


class SourceService:
    """Gestion des Sources documentaires et de leur rattachement (Exigence 10)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Création d'une Source (Exigences 10.1, 10.2, 10.4)                  #
    # ------------------------------------------------------------------ #
    async def create(self, data: SourceCreate) -> Source:
        """Crée une Source à partir de ``data`` (Exigences 10.1, 10.2).

        La création est réservée à l'Administrateur ; ce contrôle est appliqué
        par la couche API via :func:`app.api.deps.require_admin` (Exigence 10.4).
        Le ``source_type`` est validé contre l'ensemble des 7 valeurs autorisées
        (Exigence 10.2) : une valeur hors ensemble lève
        :class:`InvalidSourceTypeError`.
        """
        source_type = data.source_type.value
        if source_type not in SOURCE_TYPES:
            raise InvalidSourceTypeError(source_type)

        source = Source(
            title=data.title,
            url=data.url,
            publisher=data.publisher,
            source_type=source_type,
            publication_date=data.publication_date,
            is_verified=data.is_verified,
        )
        self._session.add(source)
        await self._session.flush()
        await self._session.refresh(source)
        return source

    # ------------------------------------------------------------------ #
    # Rattachement à une Proposition (Exigences 10.3, 10.4)              #
    # ------------------------------------------------------------------ #
    async def attach_to_proposal(
        self, proposal_id: int, data: ProposalSourceAttach
    ) -> ProposalSource:
        """Rattache une Source à une Proposition avec un ``relevance_score`` (Exigence 10.3).

        Le rattachement est réservé à l'Administrateur ; ce contrôle est appliqué
        par la couche API via :func:`app.api.deps.require_admin` (Exigence 10.4).
        L'existence de la Proposition et de la Source est vérifiée (sinon
        :class:`ProposalNotFoundError` / :class:`SourceNotFoundError`), puis
        l'association ``proposal_sources`` est enregistrée avec son
        ``relevance_score`` (Exigence 10.3).
        """
        proposal = await self._session.get(Proposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(str(proposal_id))

        source = await self._session.get(Source, data.source_id)
        if source is None:
            raise SourceNotFoundError(str(data.source_id))

        link = ProposalSource(
            proposal_id=proposal_id,
            source_id=data.source_id,
            relevance_score=data.relevance_score,
            note=data.note,
        )
        self._session.add(link)
        await self._session.flush()
        await self._session.refresh(link)
        return link

    # ------------------------------------------------------------------ #
    # Liste des Sources (Exigence 10.1)                                   #
    # ------------------------------------------------------------------ #
    async def list_sources(self, *, source_type: str | None = None) -> list[Source]:
        """Retourne les Sources, filtrées le cas échéant par ``source_type`` (Exigence 10.1).

        La consultation est publique. Un ``source_type`` hors ensemble autorisé
        lève :class:`InvalidSourceTypeError` afin de refuser un filtre invalide
        plutôt que de renvoyer silencieusement une liste vide (Exigence 10.2).
        Les Sources sont ordonnées par identifiant décroissant (plus récentes en
        tête) pour un ordre déterministe.
        """
        statement = select(Source)
        if source_type is not None:
            if source_type not in SOURCE_TYPES:
                raise InvalidSourceTypeError(source_type)
            statement = statement.where(Source.source_type == source_type)

        result = await self._session.scalars(statement.order_by(Source.id.desc()))
        return list(result.all())
