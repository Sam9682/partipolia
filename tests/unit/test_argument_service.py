"""Tests unitaires du ``ArgumentService`` (Exigence 8).

Couvre le cœur métier du Service, sans base de données réelle :

* création d'un Argument avec position ``FOR``/``AGAINST`` (Exigence 8.1) ;
* refus d'une position hors ``{FOR, AGAINST}`` (Exigence 8.1) ;
* refus de création sur une Proposition inexistante (⇒ 404) ;
* lecture d'un Argument, ``404`` si absent (Exigence 8.2) ;
* modification et suppression par l'auteur ou un Administrateur (Exigence 8.3) ;
* refus (``403``) de modification/suppression par un tiers non-auteur et
  non-Administrateur (Exigence 8.3).

La logique testée est celle du Service ; seule la couche d'accès aux données est
remplacée par une session factice reproduisant le comportement des méthodes
``get``/``add``/``flush``/``refresh``/``delete``/``scalars`` d'une
``AsyncSession``. Aucun code du Service n'est mocké.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.models.argument import Argument
from app.models.proposal import Proposal
from app.services.argument_service import (
    ArgumentNotFoundError,
    ArgumentPermissionError,
    ArgumentService,
    InvalidArgumentPositionError,
    ProposalNotFoundError,
)


@dataclass
class _Actor:
    """Substitut d'``User`` exposant uniquement ``id`` et ``is_admin``."""

    id: int
    is_admin: bool = False


@dataclass
class _Scalars:
    """Résultat de ``scalars`` exposant ``all()``."""

    rows: list[Argument]

    def all(self) -> list[Argument]:
        return self.rows


@dataclass
class _FakeSession:
    """``AsyncSession`` factice reproduisant le strict nécessaire au Service.

    ``get`` résout Propositions et Arguments depuis des tables en mémoire ; ``add``
    matérialise un id auto-incrémenté ; ``delete`` retire l'Argument ; ``scalars``
    renvoie les Arguments d'une Proposition triés par id. Aucune requête SQL réelle
    n'est exécutée.
    """

    proposals: dict[int, Proposal] = field(default_factory=dict)
    arguments: dict[int, Argument] = field(default_factory=dict)
    _next_id: int = 1

    async def get(self, model: type, pk: int) -> object | None:
        if model is Proposal:
            return self.proposals.get(pk)
        if model is Argument:
            return self.arguments.get(pk)
        raise AssertionError(f"get inattendu pour le modèle {model!r}")

    def add(self, obj: object) -> None:
        if isinstance(obj, Argument):
            if obj.id is None:
                obj.id = self._next_id
                self._next_id += 1
            self.arguments[obj.id] = obj

    async def flush(self) -> None:
        return None

    async def refresh(self, _obj: object) -> None:
        return None

    async def delete(self, obj: object) -> None:
        if isinstance(obj, Argument) and obj.id in self.arguments:
            del self.arguments[obj.id]

    async def scalars(self, _statement: object) -> _Scalars:
        rows = sorted(self.arguments.values(), key=lambda a: a.id)
        return _Scalars(rows)


def _session_with_proposal(proposal_id: int = 1) -> _FakeSession:
    session = _FakeSession()
    session.proposals[proposal_id] = Proposal(id=proposal_id)
    return session


@pytest.mark.unit
async def test_create_persists_for_position() -> None:
    """Créer un Argument enregistre la position FOR et le contenu (Exigence 8.1)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]

    argument = await service.create(_Actor(id=7), 1, "FOR", "Contenu pour")

    assert argument.id is not None
    assert argument.proposal_id == 1
    assert argument.author_id == 7
    assert argument.position == "FOR"
    assert argument.content == "Contenu pour"


@pytest.mark.unit
async def test_create_persists_against_position() -> None:
    """La position AGAINST est également acceptée (Exigence 8.1)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]

    argument = await service.create(_Actor(id=1), 1, "AGAINST", "Contenu contre")

    assert argument.position == "AGAINST"


@pytest.mark.unit
async def test_create_rejects_invalid_position() -> None:
    """Une position hors {FOR, AGAINST} est refusée (Exigence 8.1)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]

    with pytest.raises(InvalidArgumentPositionError):
        await service.create(_Actor(id=1), 1, "MAYBE", "Contenu")


@pytest.mark.unit
async def test_create_rejects_unknown_proposal() -> None:
    """Créer sur une Proposition inexistante lève ProposalNotFoundError (⇒ 404)."""
    session = _FakeSession()
    service = ArgumentService(session)  # type: ignore[arg-type]

    with pytest.raises(ProposalNotFoundError):
        await service.create(_Actor(id=1), 999, "FOR", "Contenu")


@pytest.mark.unit
async def test_get_returns_argument() -> None:
    """``get`` renvoie l'Argument existant (Exigence 8.2)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=3), 1, "FOR", "Contenu")

    fetched = await service.get(created.id)

    assert fetched.id == created.id


@pytest.mark.unit
async def test_get_missing_raises_not_found() -> None:
    """``get`` sur un id absent lève ArgumentNotFoundError (⇒ 404, Exigence 8.2)."""
    session = _FakeSession()
    service = ArgumentService(session)  # type: ignore[arg-type]

    with pytest.raises(ArgumentNotFoundError):
        await service.get(1234)


@pytest.mark.unit
async def test_list_for_proposal_orders_by_id() -> None:
    """``list_for_proposal`` renvoie les Arguments par ordre de création (Exigence 8.2)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    a1 = await service.create(_Actor(id=1), 1, "FOR", "Premier")
    a2 = await service.create(_Actor(id=2), 1, "AGAINST", "Second")

    listed = await service.list_for_proposal(1)

    assert [a.id for a in listed] == [a1.id, a2.id]


@pytest.mark.unit
async def test_author_can_update() -> None:
    """L'auteur peut modifier le contenu de son Argument (Exigences 8.2, 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Avant")

    updated = await service.update(_Actor(id=5), created.id, "Après")

    assert updated.content == "Après"


@pytest.mark.unit
async def test_admin_can_update_other_argument() -> None:
    """Un Administrateur peut modifier l'Argument d'autrui (Exigence 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Avant")

    updated = await service.update(_Actor(id=99, is_admin=True), created.id, "Après")

    assert updated.content == "Après"


@pytest.mark.unit
async def test_non_author_cannot_update() -> None:
    """Un tiers non-auteur et non-Administrateur ne peut pas modifier (⇒ 403, Exigence 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Avant")

    with pytest.raises(ArgumentPermissionError):
        await service.update(_Actor(id=6), created.id, "Interdit")


@pytest.mark.unit
async def test_author_can_delete() -> None:
    """L'auteur peut supprimer son Argument (Exigences 8.2, 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Contenu")

    await service.delete(_Actor(id=5), created.id)

    with pytest.raises(ArgumentNotFoundError):
        await service.get(created.id)


@pytest.mark.unit
async def test_admin_can_delete_other_argument() -> None:
    """Un Administrateur peut supprimer l'Argument d'autrui (Exigence 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Contenu")

    await service.delete(_Actor(id=99, is_admin=True), created.id)

    assert created.id not in session.arguments


@pytest.mark.unit
async def test_non_author_cannot_delete() -> None:
    """Un tiers non-auteur et non-Administrateur ne peut pas supprimer (⇒ 403, Exigence 8.3)."""
    session = _session_with_proposal()
    service = ArgumentService(session)  # type: ignore[arg-type]
    created = await service.create(_Actor(id=5), 1, "FOR", "Contenu")

    with pytest.raises(ArgumentPermissionError):
        await service.delete(_Actor(id=6), created.id)

    # L'Argument n'a pas été supprimé.
    assert created.id in session.arguments


@pytest.mark.unit
async def test_update_missing_raises_not_found() -> None:
    """Modifier un Argument absent lève ArgumentNotFoundError (⇒ 404, Exigence 8.2)."""
    session = _FakeSession()
    service = ArgumentService(session)  # type: ignore[arg-type]

    with pytest.raises(ArgumentNotFoundError):
        await service.update(_Actor(id=1), 404, "Contenu")


@pytest.mark.unit
async def test_delete_missing_raises_not_found() -> None:
    """Supprimer un Argument absent lève ArgumentNotFoundError (⇒ 404, Exigence 8.2)."""
    session = _FakeSession()
    service = ArgumentService(session)  # type: ignore[arg-type]

    with pytest.raises(ArgumentNotFoundError):
        await service.delete(_Actor(id=1), 404)
