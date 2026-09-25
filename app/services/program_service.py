"""Service du Programme (Exigences 18 et 19).

Ce Service implémente le contrat ``ProgramService`` de la conception :

* ``get_program`` — renvoie le Programme (unique Programme actif) (Exigence 18.1) ;
* ``list_program_proposals`` — liste les associations ``program_proposals``,
  ordonnées par ``priority`` (Exigences 18.1, 18.4) ;
* ``add_proposal`` — crée l'association Programme_Proposition avec ``priority`` et
  ``included_at`` (inclusion définitive explicite et traçable, Exigences 18.2,
  18.4) ; réservé à l'Administrateur au niveau API (:func:`app.api.deps.require_admin`) ;
* ``remove_proposal`` — supprime l'association correspondante (Exigence 18.3) ;
* ``statistics`` — statistiques agrégées et anonymisées du Programme
  (Exigence 18.5) ;
* ``build_draft_view`` — vue « Programme en construction » : **calcul de
  restitution non décisionnel** regroupant les Propositions fortement soutenues,
  suffisamment documentées, non contradictoires et estimées financièrement
  (Exigence 19). Cette vue ne décide **jamais** de l'inclusion : elle n'écrit rien
  dans ``program_proposals`` ; l'inclusion définitive reste un acte explicite via
  :meth:`add_proposal` (Exigences 19.2, 19.3).

``priority`` et ``included_at`` sont des attributs de l'association
``program_proposals`` et non de la Proposition (Exigence 18.4). Le Service est
instancié par requête avec une ``AsyncSession`` (SQLAlchemy 2.x asynchrone), avec
chargement anticipé ``selectin`` défini au niveau des modèles pour éviter le N+1.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Final

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.program import Program, ProgramProposal
from app.models.proposal import Proposal
from app.models.source import ProposalSource
from app.models.theme import Theme
from app.models.vote import Vote
from app.schemas.program import (
    DraftProgramEntry,
    DraftProgramView,
    ProgramMeasure,
    ProgramPageView,
    ProgramStatistics,
    ProgramThemeGroup,
)

# Messages d'erreur génériques (Exigences 31.1, 31.2).
PROGRAM_NOT_FOUND_ERROR: Final = "Programme introuvable."
PROPOSAL_NOT_FOUND_ERROR: Final = "Proposition introuvable."
ASSOCIATION_NOT_FOUND_ERROR: Final = "Association Programme_Proposition introuvable."

# Seuils de la vue « Programme en construction » (Exigence 19.1). Ils définissent
# ce que « fortement soutenue » signifie : un taux de soutien élevé calculé sur
# un socle minimal de participation exprimée (soutien + opposition). Ces seuils
# relèvent d'un calcul de restitution et n'induisent aucune décision d'inclusion.
DRAFT_MIN_SUPPORT_RATE: Final = 0.6
DRAFT_MIN_SUPPORT_COUNT: Final = 1


class ProgramError(Exception):
    """Erreur générique du Service du Programme."""


class ProgramNotFoundError(ProgramError):
    """Aucun Programme n'existe (le seed initial doit en créer un, Exigence 18.1)."""


class ProposalNotFoundError(ProgramError):
    """La Proposition référencée n'existe pas (Exigences 18.2, 31.2)."""


class ProgramProposalNotFoundError(ProgramError):
    """L'association Programme_Proposition à retirer n'existe pas (Exigences 18.3, 31.2)."""


class ProgramService:
    """Construction et restitution du Programme (Exigences 18 et 19)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Lecture du Programme (Exigence 18.1)                                #
    # ------------------------------------------------------------------ #
    async def get_program(self) -> Program:
        """Retourne le Programme (le plus ancien, unique en pratique) (Exigence 18.1).

        Le seed initial crée un unique Programme (statut ``DRAFT``) ; en son
        absence, :class:`ProgramNotFoundError` est levée.
        """
        program = await self._session.scalar(
            select(Program).order_by(Program.id).limit(1)
        )
        if program is None:
            raise ProgramNotFoundError()
        return program

    # ------------------------------------------------------------------ #
    # Liste des associations Programme_Proposition (Exigences 18.1, 18.4) #
    # ------------------------------------------------------------------ #
    async def list_program_proposals(self) -> list[ProgramProposal]:
        """Liste les associations du Programme, ordonnées par ``priority`` (Exigence 18.1).

        ``priority`` et ``included_at`` sont des attributs de l'association
        (Exigence 18.4).
        """
        program = await self.get_program()
        result = await self._session.scalars(
            select(ProgramProposal)
            .where(ProgramProposal.program_id == program.id)
            .order_by(ProgramProposal.priority, ProgramProposal.id)
        )
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Inclusion d'une Proposition (Exigences 18.2, 18.4)                  #
    # ------------------------------------------------------------------ #
    async def add_proposal(self, proposal_id: int, priority: int = 0) -> ProgramProposal:
        """Ajoute une Proposition au Programme (Exigences 18.2, 18.4).

        Crée l'association ``program_proposals`` avec ``priority`` et un
        ``included_at`` horodaté : l'inclusion définitive est ainsi **explicite et
        traçable** (Exigences 18.2, 19.3). La réservation à l'Administrateur est
        appliquée par la couche API (:func:`app.api.deps.require_admin`).

        Une Proposition inexistante lève :class:`ProposalNotFoundError`. Une
        Proposition déjà incluse voit sa ``priority`` mise à jour (ré-inclusion
        idempotente) plutôt que dupliquée, l'unicité étant portée par le couple
        ``(program_id, proposal_id)``.
        """
        program = await self.get_program()

        proposal = await self._session.get(Proposal, proposal_id)
        if proposal is None:
            raise ProposalNotFoundError(str(proposal_id))

        existing = await self._session.scalar(
            select(ProgramProposal).where(
                ProgramProposal.program_id == program.id,
                ProgramProposal.proposal_id == proposal_id,
            )
        )
        if existing is not None:
            existing.priority = priority
            if existing.included_at is None:
                existing.included_at = datetime.now(timezone.utc)
            await self._session.flush()
            await self._session.refresh(existing)
            return existing

        association = ProgramProposal(
            program_id=program.id,
            proposal_id=proposal_id,
            priority=priority,
            included_at=datetime.now(timezone.utc),
        )
        self._session.add(association)
        await self._session.flush()
        await self._session.refresh(association)
        return association

    # ------------------------------------------------------------------ #
    # Retrait d'une Proposition (Exigence 18.3)                           #
    # ------------------------------------------------------------------ #
    async def remove_proposal(self, proposal_id: int) -> None:
        """Retire une Proposition du Programme (Exigence 18.3).

        Supprime l'association ``program_proposals`` correspondante. En son
        absence, :class:`ProgramProposalNotFoundError` est levée. La réservation à
        l'Administrateur est appliquée par la couche API.
        """
        program = await self.get_program()
        association = await self._session.scalar(
            select(ProgramProposal).where(
                ProgramProposal.program_id == program.id,
                ProgramProposal.proposal_id == proposal_id,
            )
        )
        if association is None:
            raise ProgramProposalNotFoundError(str(proposal_id))

        await self._session.delete(association)
        await self._session.flush()

    # ------------------------------------------------------------------ #
    # Statistiques du Programme (Exigence 18.5)                           #
    # ------------------------------------------------------------------ #
    async def statistics(self) -> ProgramStatistics:
        """Calcule les statistiques agrégées et anonymisées du Programme (Exigence 18.5).

        Les décomptes sont globaux (nombre d'inclusions, d'inclusions
        définitives, de Thèmes couverts) et ne reconstituent aucune donnée
        individuelle.
        """
        program = await self.get_program()

        included_count = await self._session.scalar(
            select(func.count())
            .select_from(ProgramProposal)
            .where(ProgramProposal.program_id == program.id)
        )
        definitive_count = await self._session.scalar(
            select(func.count())
            .select_from(ProgramProposal)
            .where(
                ProgramProposal.program_id == program.id,
                ProgramProposal.included_at.is_not(None),
            )
        )
        theme_count = await self._session.scalar(
            select(func.count(func.distinct(Proposal.theme_id)))
            .select_from(ProgramProposal)
            .join(Proposal, Proposal.id == ProgramProposal.proposal_id)
            .where(ProgramProposal.program_id == program.id)
        )

        return ProgramStatistics(
            program_id=program.id,
            included_count=int(included_count or 0),
            definitive_count=int(definitive_count or 0),
            theme_count=int(theme_count or 0),
        )

    # ------------------------------------------------------------------ #
    # Vue « Programme en construction » (Exigence 19) — NON décisionnelle #
    # ------------------------------------------------------------------ #
    async def build_draft_view(self) -> DraftProgramView:
        """Calcule la vue « Programme en construction » (Exigence 19).

        Regroupe les Propositions **fortement soutenues** (taux de soutien élevé
        sur un socle minimal de participation), **suffisamment documentées** (au
        moins une Source rattachée), **non contradictoires** (le soutien exprimé
        domine l'opposition, garanti par le seuil de taux) et **estimées
        financièrement** (au moins un montant renseigné) (Exigence 19.1).

        Ce calcul est **non décisionnel** : il n'écrit rien dans
        ``program_proposals`` et la vue renvoyée porte ``is_definitive=False``
        (Exigence 19.2). L'inclusion définitive reste explicite via
        :meth:`add_proposal` (Exigence 19.3).
        """
        # Sous-requête d'agrégation des Votes de soutien/opposition par Proposition.
        support_expr = func.coalesce(
            func.sum(case((Vote.value == 1, 1), else_=0)), 0
        ).label("support_count")
        oppose_expr = func.coalesce(
            func.sum(case((Vote.value == -1, 1), else_=0)), 0
        ).label("oppose_count")
        vote_agg = (
            select(
                Vote.proposal_id.label("proposal_id"),
                support_expr,
                oppose_expr,
            )
            .group_by(Vote.proposal_id)
            .subquery()
        )

        # Sous-requête de comptage des Sources documentaires par Proposition.
        source_agg = (
            select(
                ProposalSource.proposal_id.label("proposal_id"),
                func.count().label("source_count"),
            )
            .group_by(ProposalSource.proposal_id)
            .subquery()
        )

        support_count = func.coalesce(vote_agg.c.support_count, 0)
        oppose_count = func.coalesce(vote_agg.c.oppose_count, 0)
        source_count = func.coalesce(source_agg.c.source_count, 0)

        # « Estimée financièrement » : au moins un montant renseigné (Exigence 19.1).
        is_estimated = or_(
            Proposal.estimated_cost.is_not(None),
            Proposal.estimated_savings.is_not(None),
            Proposal.estimated_revenue.is_not(None),
        )

        statement: Select = (
            select(
                Proposal.id.label("proposal_id"),
                Proposal.slug,
                Proposal.title,
                Proposal.theme_id,
                support_count.label("support_count"),
                oppose_count.label("oppose_count"),
                source_count.label("source_count"),
            )
            .select_from(Proposal)
            .join(vote_agg, vote_agg.c.proposal_id == Proposal.id)
            .join(source_agg, source_agg.c.proposal_id == Proposal.id)
            .where(
                # Fortement soutenue : socle minimal de soutien (Exigence 19.1).
                support_count >= DRAFT_MIN_SUPPORT_COUNT,
                # Suffisamment documentée : au moins une Source (Exigence 19.1).
                source_count >= 1,
                # Non contradictoire : le soutien domine l'opposition (Exigence 19.1).
                support_count > oppose_count,
                # Estimée financièrement (Exigence 19.1).
                is_estimated,
            )
            .order_by(Proposal.id)
        )

        rows = (await self._session.execute(statement)).all()

        entries: list[DraftProgramEntry] = []
        for row in rows:
            denominator = row.support_count + row.oppose_count
            support_rate = (
                row.support_count / denominator if denominator > 0 else 0.0
            )
            # Filtre « fortement soutenue » sur le taux (Exigence 19.1).
            if support_rate < DRAFT_MIN_SUPPORT_RATE:
                continue
            entries.append(
                DraftProgramEntry(
                    proposal_id=row.proposal_id,
                    slug=row.slug,
                    title=row.title,
                    theme_id=row.theme_id,
                    support_count=int(row.support_count),
                    oppose_count=int(row.oppose_count),
                    support_rate=support_rate,
                    source_count=int(row.source_count),
                    is_estimated=True,
                )
            )

        # Résultat de participation, jamais un Programme définitif (Exigence 19.2).
        return DraftProgramView(entries=entries, is_definitive=False)

    # ------------------------------------------------------------------ #
    # Vue publique de la page Programme (Exigences 24.2, 24.3) — SSR      #
    # ------------------------------------------------------------------ #
    async def build_public_program_view(self) -> ProgramPageView:
        """Construit la restitution publique de la page Programme (Exigences 24.2, 24.3).

        Restitue les mesures **incluses au Programme** (associations
        ``program_proposals``) regroupées par Thème. Pour chaque mesure : décomptes
        de Votes pour/contre, participation, taux de soutien et agrégats financiers
        estimés (coût, recettes, économies). Pour chaque Thème : nombre de mesures,
        total de Votes exprimés, soutien agrégé et somme des montants estimés
        renseignés.

        C'est une **restitution factuelle** : les agrégats financiers sont des
        estimations (les hypothèses sont explicitées côté gabarit, Exigence 24.3)
        et les montants absents restent ``None`` — aucun agrégat n'est inventé. La
        vue est calculée en une passe de requête (jointures + agrégation des Votes)
        pour éviter le problème N+1.
        """
        program = await self.get_program()

        # Agrégation des Votes par Proposition (pour/contre/participation).
        support_expr = func.coalesce(
            func.sum(case((Vote.value == 1, 1), else_=0)), 0
        ).label("support_count")
        oppose_expr = func.coalesce(
            func.sum(case((Vote.value == -1, 1), else_=0)), 0
        ).label("oppose_count")
        participation_expr = func.coalesce(func.count(Vote.id), 0).label(
            "participation_count"
        )
        vote_agg = (
            select(
                Vote.proposal_id.label("proposal_id"),
                support_expr,
                oppose_expr,
                participation_expr,
            )
            .group_by(Vote.proposal_id)
            .subquery()
        )

        support_count = func.coalesce(vote_agg.c.support_count, 0)
        oppose_count = func.coalesce(vote_agg.c.oppose_count, 0)
        participation_count = func.coalesce(vote_agg.c.participation_count, 0)

        statement: Select = (
            select(
                Theme.id.label("theme_id"),
                Theme.slug.label("theme_slug"),
                Theme.name.label("theme_name"),
                Proposal.id.label("proposal_id"),
                Proposal.slug.label("proposal_slug"),
                Proposal.title.label("proposal_title"),
                Proposal.estimated_cost,
                Proposal.estimated_revenue,
                Proposal.estimated_savings,
                ProgramProposal.priority,
                support_count.label("support_count"),
                oppose_count.label("oppose_count"),
                participation_count.label("participation_count"),
            )
            .select_from(ProgramProposal)
            .join(Proposal, Proposal.id == ProgramProposal.proposal_id)
            .join(Theme, Theme.id == Proposal.theme_id)
            .outerjoin(vote_agg, vote_agg.c.proposal_id == Proposal.id)
            .where(ProgramProposal.program_id == program.id)
            .order_by(Theme.id, ProgramProposal.priority, Proposal.id)
        )

        rows = (await self._session.execute(statement)).all()

        # Regroupement par Thème en préservant l'ordre d'apparition (priorité).
        groups: dict[int, ProgramThemeGroup] = {}
        for row in rows:
            group = groups.get(row.theme_id)
            if group is None:
                group = ProgramThemeGroup(
                    theme_id=row.theme_id,
                    slug=row.theme_slug,
                    name=row.theme_name,
                    measure_count=0,
                    proposal_count=0,
                    vote_count=0,
                    support_count=0,
                    oppose_count=0,
                    support_rate=0.0,
                    estimated_cost=None,
                    estimated_revenue=None,
                    estimated_savings=None,
                    measures=[],
                )
                groups[row.theme_id] = group

            s_count = int(row.support_count or 0)
            o_count = int(row.oppose_count or 0)
            p_count = int(row.participation_count or 0)
            denom = s_count + o_count
            measure_rate = s_count / denom if denom > 0 else 0.0

            cost = float(row.estimated_cost) if row.estimated_cost is not None else None
            revenue = (
                float(row.estimated_revenue)
                if row.estimated_revenue is not None
                else None
            )
            savings = (
                float(row.estimated_savings)
                if row.estimated_savings is not None
                else None
            )

            group.measures.append(
                ProgramMeasure(
                    proposal_id=row.proposal_id,
                    slug=row.proposal_slug,
                    title=row.proposal_title,
                    support_count=s_count,
                    oppose_count=o_count,
                    participation_count=p_count,
                    support_rate=measure_rate,
                    estimated_cost=cost,
                    estimated_revenue=revenue,
                    estimated_savings=savings,
                )
            )

            # Cumuls du Thème (les montants ``None`` n'entrent pas dans la somme).
            group.measure_count += 1
            group.proposal_count += 1
            group.vote_count += p_count
            group.support_count += s_count
            group.oppose_count += o_count
            if cost is not None:
                group.estimated_cost = (group.estimated_cost or 0.0) + cost
            if revenue is not None:
                group.estimated_revenue = (group.estimated_revenue or 0.0) + revenue
            if savings is not None:
                group.estimated_savings = (group.estimated_savings or 0.0) + savings

        # Taux de soutien agrégé par Thème + totaux du Programme.
        total_measures = 0
        total_votes = 0
        total_cost: float | None = None
        total_revenue: float | None = None
        total_savings: float | None = None
        for group in groups.values():
            group_denom = group.support_count + group.oppose_count
            group.support_rate = (
                group.support_count / group_denom if group_denom > 0 else 0.0
            )
            total_measures += group.measure_count
            total_votes += group.vote_count
            if group.estimated_cost is not None:
                total_cost = (total_cost or 0.0) + group.estimated_cost
            if group.estimated_revenue is not None:
                total_revenue = (total_revenue or 0.0) + group.estimated_revenue
            if group.estimated_savings is not None:
                total_savings = (total_savings or 0.0) + group.estimated_savings

        return ProgramPageView(
            program_id=program.id,
            themes=list(groups.values()),
            total_measure_count=total_measures,
            total_vote_count=total_votes,
            total_estimated_cost=total_cost,
            total_estimated_revenue=total_revenue,
            total_estimated_savings=total_savings,
        )
