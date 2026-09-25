"""Service des Équipes (Exigence 20).

Ce Service implémente le contrat ``TeamService`` de la conception :

* ``list_teams`` — liste publique des Équipes avec leurs membres (Exigences 20.1,
  20.2) ;
* ``create_team`` — crée une Équipe (réservé à l'Administrateur, Exigence 20.3) ;
* ``add_member`` — ajoute un membre (``role``, ``bio``) à une Équipe (réservé à
  l'Administrateur, Exigences 20.2, 20.3) ;
* ``remove_member`` — retire un membre d'une Équipe (Exigence 20.3).

Chaque création, modification ou suppression d'Équipe ou de membre est consignée
dans le Journal_D_Audit via l':class:`~app.services.audit_service.AuditService`
(Exigence 20.3). La consultation (``list_teams``) est publique et n'écrit rien.

Le Service est instancié par requête avec une ``AsyncSession`` (SQLAlchemy 2.x
async).
"""

from __future__ import annotations

from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.team import Team, TeamMember
from app.services.audit_service import AuditService

# Messages d'erreur génériques (Exigences 31.1, 31.2).
TEAM_NOT_FOUND_ERROR: Final = "Équipe introuvable."
TEAM_MEMBER_NOT_FOUND_ERROR: Final = "Membre d'Équipe introuvable."

# Libellés d'action consignés dans le Journal_D_Audit (Exigence 20.3).
_ACTION_CREATE_TEAM: Final = "TEAM_CREATE"
_ACTION_ADD_MEMBER: Final = "TEAM_MEMBER_ADD"
_ACTION_REMOVE_MEMBER: Final = "TEAM_MEMBER_REMOVE"


class TeamError(Exception):
    """Erreur générique du Service des Équipes."""


class TeamNotFoundError(TeamError):
    """L'Équipe référencée n'existe pas (Exigence 31.2)."""


class TeamMemberNotFoundError(TeamError):
    """Le membre d'Équipe référencé n'existe pas (Exigence 31.2)."""


class TeamService:
    """Gestion des Équipes et de leurs membres (Exigence 20)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # Liste des Équipes (Exigences 20.1, 20.2) — consultation publique    #
    # ------------------------------------------------------------------ #
    async def list_teams(self) -> list[Team]:
        """Retourne toutes les Équipes avec leurs membres (Exigences 20.1, 20.2).

        Les membres sont chargés en anticipé (``lazy="selectin"``). Les Équipes
        sont ordonnées par identifiant pour un ordre déterministe.
        """
        result = await self._session.scalars(select(Team).order_by(Team.id))
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Création d'une Équipe (Exigence 20.3)                               #
    # ------------------------------------------------------------------ #
    async def create_team(self, name: str, *, ip_hash: str | None = None) -> Team:
        """Crée une Équipe nommée ``name`` et consigne la modification (Exigence 20.3).

        La création est réservée à l'Administrateur ; ce contrôle est appliqué par
        la couche API via :func:`app.api.deps.require_admin`. La modification est
        enregistrée dans le Journal_D_Audit (Exigence 20.3).
        """
        team = Team(name=name)
        self._session.add(team)
        await self._session.flush()
        await self._session.refresh(team)

        await self._audit.record(
            action=_ACTION_CREATE_TEAM,
            entity_type="team",
            entity_id=team.id,
            new_data={"name": team.name},
            ip_hash=ip_hash,
        )
        return team

    # ------------------------------------------------------------------ #
    # Ajout d'un membre (Exigences 20.2, 20.3)                            #
    # ------------------------------------------------------------------ #
    async def add_member(
        self,
        team_id: int,
        *,
        user_id: int,
        role: str | None = None,
        bio: str | None = None,
        ip_hash: str | None = None,
    ) -> TeamMember:
        """Ajoute un membre (``role``, ``bio``) à une Équipe (Exigences 20.2, 20.3).

        Vérifie l'existence de l'Équipe (:class:`TeamNotFoundError` sinon), crée le
        membre puis consigne la modification dans le Journal_D_Audit (Exigence 20.3).
        """
        team = await self._session.get(Team, team_id)
        if team is None:
            raise TeamNotFoundError(str(team_id))

        member = TeamMember(team_id=team_id, user_id=user_id, role=role, bio=bio)
        self._session.add(member)
        await self._session.flush()
        await self._session.refresh(member)

        await self._audit.record(
            action=_ACTION_ADD_MEMBER,
            entity_type="team_member",
            entity_id=member.id,
            new_data={
                "team_id": member.team_id,
                "user_id": member.user_id,
                "role": member.role,
                "bio": member.bio,
            },
            ip_hash=ip_hash,
        )
        return member

    # ------------------------------------------------------------------ #
    # Suppression d'un membre (Exigence 20.3)                             #
    # ------------------------------------------------------------------ #
    async def remove_member(
        self, member_id: int, *, ip_hash: str | None = None
    ) -> None:
        """Retire un membre d'une Équipe et consigne la modification (Exigence 20.3).

        Lève :class:`TeamMemberNotFoundError` si le membre n'existe pas.
        """
        member = await self._session.get(TeamMember, member_id)
        if member is None:
            raise TeamMemberNotFoundError(str(member_id))

        old_data = {
            "team_id": member.team_id,
            "user_id": member.user_id,
            "role": member.role,
            "bio": member.bio,
        }
        await self._session.delete(member)
        await self._session.flush()

        await self._audit.record(
            action=_ACTION_REMOVE_MEMBER,
            entity_type="team_member",
            entity_id=member_id,
            old_data=old_data,
            ip_hash=ip_hash,
        )
