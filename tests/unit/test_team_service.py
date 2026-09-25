"""Tests unitaires du ``TeamService`` (Exigence 20).

Couvre la logique du Service sans base de données réelle, via une ``AsyncSession``
factice n'implémentant que les opérations utilisées (``get``, ``add``, ``flush``,
``refresh``, ``delete``, ``scalars``). Le vrai code du Service **et** de
l'``AuditService`` est exercé (aucun mock de logique) :

* ``create_team`` crée l'Équipe et consigne la modification (Exigence 20.3) ;
* ``add_member`` ajoute un membre avec ``role`` et ``bio`` (Exigence 20.2), vérifie
  l'existence de l'Équipe (``404`` sinon) et consigne la modification (Exigence 20.3) ;
* ``remove_member`` retire un membre et consigne la suppression (Exigence 20.3) ;
* ``list_teams`` renvoie les Équipes ordonnées (Exigences 20.1, 20.2).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.audit import AuditLog
from app.models.team import Team, TeamMember
from app.services.team_service import (
    TeamMemberNotFoundError,
    TeamNotFoundError,
    TeamService,
)


class _FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeSession:
    """``AsyncSession`` factice avec magasin en mémoire (Équipes, membres, audit)."""

    def __init__(self) -> None:
        self.teams: dict[int, Team] = {}
        self.members: dict[int, TeamMember] = {}
        self.audit_logs: list[AuditLog] = []
        self._next_team_id = 1
        self._next_member_id = 1

    async def get(self, model: type, ident: int) -> object | None:
        if model is Team:
            return self.teams.get(ident)
        if model is TeamMember:
            return self.members.get(ident)
        return None

    def add(self, obj: object) -> None:
        now = datetime.now(timezone.utc)
        if isinstance(obj, Team):
            if obj.id is None:
                obj.id = self._next_team_id
                self._next_team_id += 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = now
                obj.updated_at = now
            self.teams[obj.id] = obj
        elif isinstance(obj, TeamMember):
            if obj.id is None:
                obj.id = self._next_member_id
                self._next_member_id += 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = now
                obj.updated_at = now
            self.members[obj.id] = obj
        elif isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: object) -> None:
        return None

    async def delete(self, obj: object) -> None:
        if isinstance(obj, TeamMember) and obj.id in self.members:
            del self.members[obj.id]

    async def scalars(self, _statement: object) -> _FakeScalarResult:
        rows = sorted(self.teams.values(), key=lambda t: t.id)
        return _FakeScalarResult(rows)


@pytest.mark.unit
async def test_create_team_records_audit() -> None:
    """La création d'une Équipe crée l'entité et consigne la modification (Exigence 20.3)."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    team = await service.create_team("Équipe A")

    assert team.id is not None
    assert team.name == "Équipe A"
    assert len(session.audit_logs) == 1
    entry = session.audit_logs[0]
    assert entry.action == "TEAM_CREATE"
    assert entry.entity_type == "team"
    assert entry.entity_id == team.id


@pytest.mark.unit
async def test_add_member_with_role_and_bio_records_audit() -> None:
    """Un membre est ajouté avec role/bio et la modification est consignée (Exigences 20.2, 20.3)."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    team = await service.create_team("Équipe A")
    member = await service.add_member(
        team.id, user_id=42, role="Porte-parole", bio="Bio courte."
    )

    assert member.team_id == team.id
    assert member.user_id == 42
    assert member.role == "Porte-parole"
    assert member.bio == "Bio courte."
    actions = [log.action for log in session.audit_logs]
    assert actions == ["TEAM_CREATE", "TEAM_MEMBER_ADD"]


@pytest.mark.unit
async def test_add_member_on_missing_team_raises_not_found() -> None:
    """Ajouter un membre à une Équipe inexistante lève ``TeamNotFoundError``."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    with pytest.raises(TeamNotFoundError):
        await service.add_member(999, user_id=1, role="X")

    # Aucune modification consignée puisque l'ajout a échoué.
    assert session.audit_logs == []


@pytest.mark.unit
async def test_remove_member_records_audit() -> None:
    """Le retrait d'un membre supprime l'entité et consigne la modification (Exigence 20.3)."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    team = await service.create_team("Équipe A")
    member = await service.add_member(team.id, user_id=7, role="Trésorier")

    await service.remove_member(member.id)

    assert member.id not in session.members
    assert session.audit_logs[-1].action == "TEAM_MEMBER_REMOVE"
    assert session.audit_logs[-1].entity_id == member.id


@pytest.mark.unit
async def test_remove_missing_member_raises_not_found() -> None:
    """Retirer un membre inexistant lève ``TeamMemberNotFoundError``."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    with pytest.raises(TeamMemberNotFoundError):
        await service.remove_member(12345)


@pytest.mark.unit
async def test_list_teams_returns_ordered_teams() -> None:
    """``list_teams`` renvoie les Équipes ordonnées par identifiant (Exigences 20.1, 20.2)."""
    session = _FakeSession()
    service = TeamService(session)  # type: ignore[arg-type]

    await service.create_team("Première")
    await service.create_team("Seconde")

    teams = await service.list_teams()

    assert [t.name for t in teams] == ["Première", "Seconde"]
