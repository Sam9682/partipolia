"""Service des Propositions (Exigences 3 et 4).

Ce Service implémente le contrat ``ProposalService`` de la conception :

* ``create`` — crée une Proposition à l'état ``DRAFT``, version ``1``, avec un
  ``slug`` unique attribué (Exigences 3.2, 4.1), et enregistre l'instantané de la
  version initiale dans ``proposal_versions`` (Exigences 3.4, 3.5) ;
* ``get`` — renvoie le détail d'une Proposition, historique compris
  (Exigence 4.4) ;
* ``list`` — liste paginée et filtrée (``theme`` / ``status`` / ``sort`` /
  ``search``) (Exigence 4.3) ; le tri ne s'appuie **jamais** sur
  ``ORDER BY vote_count`` (Exigence 7.3) ;
* ``update`` — applique une modification, crée une ``ProposalVersion`` complète
  avec ``change_summary`` et **incrémente** ``version`` sans jamais écraser une
  version antérieure (Exigences 3.4, 3.5, 4.5) ;
* ``archive`` — fait passer la Proposition au statut ``ARCHIVED`` en conservant
  son historique (Exigence 4.6).

Contrôle d'accès (Exigence 4.7) : seuls l'auteur de la Proposition ou un
Administrateur peuvent la modifier (``update``) ou l'archiver (``archive``) ;
toute autre tentative lève :class:`ProposalPermissionError`.

Le Service est instancié par requête avec une ``AsyncSession`` ; SQLAlchemy 2.x
en style asynchrone, avec chargement anticipé ``selectin`` sur les relations
(défini au niveau des modèles) pour éviter le N+1.
"""

from __future__ import annotations

import re
import secrets
import unicodedata
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposal import PROPOSAL_STATUSES, Proposal, ProposalVersion
from app.models.user import User
from app.schemas.proposal import (
    PROPOSAL_SORTS,
    Page,
    ProposalCreate,
    ProposalDetail,
    ProposalSummary,
    ProposalUpdate,
)

# Champs métier composant l'instantané (``snapshot``) d'une version (Exigence 3.4).
# Ils reflètent l'intégralité des attributs de contenu d'une Proposition
# (Exigence 3.1) hors métadonnées de gestion (id, slug, horodatages).
_SNAPSHOT_FIELDS: Final[tuple[str, ...]] = (
    "theme_id",
    "title",
    "problem",
    "description",
    "expected_impact",
    "implementation_delay",
    "estimated_cost",
    "estimated_savings",
    "estimated_revenue",
    "funding_description",
    "legal_constraints",
    "status",
    "version",
)

# Longueur maximale de la portion « titre » d'un slug avant le suffixe éventuel.
_SLUG_MAX_LENGTH: Final = 200

# Résumé par défaut de la version initiale d'une Proposition (Exigence 3.4).
_INITIAL_CHANGE_SUMMARY: Final = "Création de la proposition"

# Messages d'erreur.
NOT_FOUND_ERROR: Final = "Proposition introuvable."
FORBIDDEN_ERROR: Final = "Action réservée à l'auteur ou à un administrateur."


class ProposalError(Exception):
    """Erreur générique du Service des Propositions."""


class ProposalNotFoundError(ProposalError):
    """La Proposition demandée n'existe pas (⇒ 404, Exigence 31.2)."""


class ProposalPermissionError(ProposalError):
    """Action réservée à l'auteur ou à un Administrateur (⇒ 403, Exigence 4.7)."""


class ProposalService:
    """Logique des Propositions : création, versionnement, archivage, liste.

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Création (Exigences 3.2, 4.1)                                       #
    # ------------------------------------------------------------------ #
    async def create(self, author: User, data: ProposalCreate) -> Proposal:
        """Crée une Proposition à l'état ``DRAFT``, version ``1``, slug unique.

        Un instantané de la version initiale est enregistré dans
        ``proposal_versions`` afin que l'historique parte de la version 1
        (Exigences 3.2, 3.4, 3.5, 4.1).
        """
        slug = await self._make_unique_slug(data.title)
        proposal = Proposal(
            theme_id=data.theme_id,
            author_id=author.id,
            slug=slug,
            title=data.title,
            problem=data.problem,
            description=data.description,
            expected_impact=data.expected_impact,
            implementation_delay=data.implementation_delay,
            estimated_cost=data.estimated_cost,
            estimated_savings=data.estimated_savings,
            estimated_revenue=data.estimated_revenue,
            funding_description=data.funding_description,
            legal_constraints=data.legal_constraints,
            status="DRAFT",
            version=1,
        )
        self._session.add(proposal)
        # Flush pour matérialiser l'id, requis par l'instantané de version.
        await self._session.flush()

        version = self._snapshot_version(
            proposal, change_summary=_INITIAL_CHANGE_SUMMARY, editor_id=author.id
        )
        self._session.add(version)
        await self._session.flush()
        return proposal

    # ------------------------------------------------------------------ #
    # Détail (Exigence 4.4)                                               #
    # ------------------------------------------------------------------ #
    async def get(self, proposal_id: int) -> ProposalDetail:
        """Retourne le détail d'une Proposition, historique compris (Exigence 4.4)."""
        proposal = await self._get_or_404(proposal_id)
        return ProposalDetail.model_validate(proposal)

    # ------------------------------------------------------------------ #
    # Liste filtrée et paginée (Exigences 4.3, 7.3)                       #
    # ------------------------------------------------------------------ #
    async def list(
        self,
        *,
        theme: str | None = None,
        status: str | None = None,
        sort: str = "recent",
        search: str | None = None,
        page: int = 1,
        limit: int = 20,
    ) -> Page[ProposalSummary]:
        """Liste paginée et filtrée des Propositions (Exigence 4.3).

        Filtres : ``theme`` (slug du Thème), ``status``, ``search`` (titre).
        Le tri (``sort``) porte uniquement sur des champs stables (dates, titre)
        et n'utilise **jamais** ``ORDER BY vote_count`` (Exigence 7.3) ; une
        valeur de tri inconnue retombe sur « recent » (plus récent d'abord).
        """
        page = max(page, 1)
        limit = max(limit, 1)

        conditions = []
        if status is not None:
            conditions.append(Proposal.status == status)
        if theme is not None:
            # Filtre par slug de Thème via la relation.
            conditions.append(Proposal.theme.has(slug=theme))
        if search:
            conditions.append(Proposal.title.ilike(f"%{search}%"))

        # Décompte total (avant pagination) pour l'enveloppe de page.
        count_stmt = select(func.count()).select_from(Proposal)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
        total = int((await self._session.scalar(count_stmt)) or 0)

        stmt = select(Proposal)
        if conditions:
            stmt = stmt.where(*conditions)
        stmt = stmt.order_by(*self._order_by(sort))
        stmt = stmt.offset((page - 1) * limit).limit(limit)

        rows = (await self._session.scalars(stmt)).all()
        items = [ProposalSummary.model_validate(row) for row in rows]
        return Page(items=items, total=total, page=page, limit=limit)

    # ------------------------------------------------------------------ #
    # Mise à jour → nouvelle version (Exigences 3.4, 3.5, 4.5, 4.7)       #
    # ------------------------------------------------------------------ #
    async def update(self, actor: User, proposal_id: int, data: ProposalUpdate) -> Proposal:
        """Applique une modification et crée une nouvelle version (Exigences 3.4, 4.5).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent modifier
        (Exigence 4.7). L'ancienne version est **préservée** : une nouvelle
        ``ProposalVersion`` est ajoutée avec ``change_summary`` et ``version`` est
        incrémenté (Exigences 3.4, 3.5).
        """
        proposal = await self._get_or_404(proposal_id)
        self._authorize(actor, proposal)

        # Applique uniquement les champs explicitement fournis (mise à jour partielle).
        changes = data.model_dump(exclude_unset=True, exclude={"change_summary"})
        for field, value in changes.items():
            setattr(proposal, field, value)

        # Incrémente la version puis enregistre l'instantané correspondant à ce
        # nouvel état, sans écraser les versions antérieures (Exigences 3.4, 3.5).
        proposal.version += 1
        version = self._snapshot_version(
            proposal, change_summary=data.change_summary, editor_id=actor.id
        )
        self._session.add(version)
        await self._session.flush()
        return proposal

    # ------------------------------------------------------------------ #
    # Archivage (Exigences 4.6, 4.7)                                      #
    # ------------------------------------------------------------------ #
    async def archive(self, actor: User, proposal_id: int) -> Proposal:
        """Fait passer la Proposition au statut ``ARCHIVED`` (Exigences 4.6, 4.7).

        Contrôle d'accès : seuls l'auteur ou un Administrateur peuvent archiver
        (Exigence 4.7). L'historique des versions est conservé intact.
        """
        proposal = await self._get_or_404(proposal_id)
        self._authorize(actor, proposal)

        proposal.status = "ARCHIVED"
        await self._session.flush()
        return proposal

    # ------------------------------------------------------------------ #
    # Utilitaires internes                                                #
    # ------------------------------------------------------------------ #
    def _snapshot_version(
        self, proposal: Proposal, *, change_summary: str | None, editor_id: int | None
    ) -> ProposalVersion:
        """Construit un instantané complet et immuable de l'état courant (Exigence 3.4).

        Le ``snapshot`` (jsonb) capture l'intégralité des champs de contenu et de
        statut de la Proposition ; ``change_summary`` décrit la modification. La
        version retournée n'est pas encore ajoutée à la session par cette méthode.
        """
        snapshot: dict[str, Any] = {}
        for field in _SNAPSHOT_FIELDS:
            value = getattr(proposal, field)
            # Les ``Decimal`` (montants estimés) sont sérialisés en chaîne pour
            # rester représentables en JSON sans perte de précision.
            snapshot[field] = str(value) if _is_decimal(value) else value

        return ProposalVersion(
            proposal_id=proposal.id,
            version=proposal.version,
            snapshot=snapshot,
            change_summary=change_summary,
            edited_by=editor_id,
        )

    def _make_slug(self, title: str) -> str:
        """Dérive un slug ASCII, minuscule et tiretté à partir d'un titre (Exigence 3.2).

        Les accents sont retirés, les caractères non alphanumériques deviennent
        des tirets, et la longueur est bornée. Un titre vide (après nettoyage)
        retombe sur ``"proposition"``.
        """
        normalized = unicodedata.normalize("NFKD", title)
        ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
        slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
        slug = slug[:_SLUG_MAX_LENGTH].strip("-")
        return slug or "proposition"

    async def _make_unique_slug(self, title: str) -> str:
        """Retourne un slug unique en base, en suffixant en cas de collision.

        ``slug`` est UNIQUE (Exigence 3.3) : si le slug de base est déjà pris, un
        suffixe aléatoire court est ajouté jusqu'à obtenir une valeur libre.
        """
        base = self._make_slug(title)
        candidate = base
        while await self._slug_exists(candidate):
            candidate = f"{base}-{secrets.token_hex(3)}"
        return candidate

    async def _slug_exists(self, slug: str) -> bool:
        """Indique si un ``slug`` est déjà utilisé par une Proposition."""
        existing = await self._session.scalar(
            select(Proposal.id).where(Proposal.slug == slug)
        )
        return existing is not None

    async def _get_or_404(self, proposal_id: int) -> Proposal:
        """Charge une Proposition ou lève :class:`ProposalNotFoundError`."""
        proposal = await self._session.get(Proposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(NOT_FOUND_ERROR)
        return proposal

    @staticmethod
    def _authorize(actor: User, proposal: Proposal) -> None:
        """Autorise l'auteur ou un Administrateur, refuse sinon (Exigence 4.7)."""
        if actor.is_admin or actor.id == proposal.author_id:
            return
        raise ProposalPermissionError(FORBIDDEN_ERROR)

    @staticmethod
    def _order_by(sort: str) -> tuple[Any, ...]:
        """Retourne la clause de tri correspondante (Exigences 4.3, 7.3).

        Le tri porte exclusivement sur des colonnes stables ; il n'existe
        volontairement aucune option de tri par nombre de votes (Exigence 7.3).
        Une valeur inconnue retombe sur « recent ».
        """
        effective = sort if sort in PROPOSAL_SORTS else "recent"
        if effective == "oldest":
            return (Proposal.created_at.asc(), Proposal.id.asc())
        if effective == "title":
            return (Proposal.title.asc(), Proposal.id.asc())
        if effective == "updated":
            return (Proposal.updated_at.desc(), Proposal.id.desc())
        # "recent" — plus récent d'abord (par défaut).
        return (Proposal.created_at.desc(), Proposal.id.desc())


def _is_decimal(value: Any) -> bool:
    """Indique si une valeur est un ``Decimal`` (montant estimé)."""
    from decimal import Decimal

    return isinstance(value, Decimal)


# Garantit que toutes les valeurs de statut sont connues du Service (Exigence 3.3).
assert "ARCHIVED" in PROPOSAL_STATUSES
