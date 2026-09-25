"""Tests unitaires du ``ProposalService`` (Exigences 3 et 4).

Couvre la logique métier des Propositions sans base de données réelle :

* **création** — état initial ``DRAFT``, ``version`` 1, ``slug`` unique dérivé du
  titre, et instantané de version 1 enregistré (Exigences 3.2, 4.1) ;
* **nouvelle version** — ``update`` applique la modification, incrémente
  ``version`` et crée une ``ProposalVersion`` complète avec ``change_summary``
  sans écraser l'historique (Exigences 3.4, 3.5, 4.5) ;
* **archivage** — ``archive`` fait passer le statut à ``ARCHIVED`` en conservant
  l'historique (Exigence 4.6) ;
* **contrôle d'accès** — seuls l'auteur ou un Administrateur peuvent modifier ou
  archiver ; tout autre acteur se voit refuser l'action (Exigence 4.7).

À l'image de ``test_popularity_service``, la couche d'accès aux données est
remplacée par une ``AsyncSession`` factice qui n'imite **pas** la logique du
Service : elle se contente de mémoriser les objets ajoutés, d'attribuer un ``id``
au flush, de résoudre ``get`` et de répondre à la vérification d'unicité du slug
(``SELECT proposals.id WHERE slug = :slug``). C'est le vrai code du Service qui
est exercé.
"""

from __future__ import annotations

import pytest

from app.models.proposal import Proposal, ProposalVersion
from app.models.user import User
from app.schemas.proposal import ProposalCreate, ProposalUpdate
from app.services.proposal_service import (
    FORBIDDEN_ERROR,
    ProposalNotFoundError,
    ProposalPermissionError,
    ProposalService,
)


# --------------------------------------------------------------------------- #
# Doublure de session : mémoire, sans logique métier                          #
# --------------------------------------------------------------------------- #
class _FakeSession:
    """``AsyncSession`` factice à mémoire pour exercer le Service hors base.

    Elle attribue un ``id`` séquentiel aux objets au ``flush`` (comme le ferait
    une séquence), résout ``get(Proposal, id)`` et répond à la requête
    d'existence de slug utilisée par ``_slug_exists`` en inspectant les
    Propositions déjà connues.
    """

    def __init__(self, *, existing_slugs: set[str] | None = None) -> None:
        self.proposals: dict[int, Proposal] = {}
        self.versions: list[ProposalVersion] = []
        self._existing_slugs: set[str] = set(existing_slugs or set())
        self._next_proposal_id = 1
        self._next_version_id = 1
        self.flush_count = 0

    def add(self, obj: object) -> None:
        if isinstance(obj, Proposal):
            if obj.id is None:
                obj.id = self._next_proposal_id
                self._next_proposal_id += 1
            self.proposals[obj.id] = obj
            self._existing_slugs.add(obj.slug)
        elif isinstance(obj, ProposalVersion):
            if obj.id is None:
                obj.id = self._next_version_id
                self._next_version_id += 1
            self.versions.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1

    async def get(self, model: type, ident: int) -> object | None:
        if model is Proposal:
            return self.proposals.get(ident)
        return None

    async def scalar(self, statement: object) -> object | None:
        """Répond à ``SELECT proposals.id WHERE slug = :slug`` (unicité du slug)."""
        params = dict(statement.compile().params)  # type: ignore[attr-defined]
        slug = next(iter(params.values()), None)
        return 1 if slug in self._existing_slugs else None


def _service(session: _FakeSession) -> ProposalService:
    return ProposalService(session)  # type: ignore[arg-type]


def _user(user_id: int, *, is_admin: bool = False) -> User:
    return User(
        id=user_id,
        email=f"u{user_id}@example.org",
        password_hash="x",
        display_name=f"User {user_id}",
        is_admin=is_admin,
    )


def _create_data(**overrides: object) -> ProposalCreate:
    base: dict[str, object] = {
        "theme_id": 1,
        "title": "Rénover les écoles publiques",
        "problem": "Bâtiments vétustes.",
        "description": "Plan pluriannuel de rénovation.",
    }
    base.update(overrides)
    return ProposalCreate(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Création (Exigences 3.2, 4.1)                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_create_initialises_draft_version_one_and_slug() -> None:
    """La création part de ``DRAFT``, ``version`` 1 et attribue un slug (Exigences 3.2, 4.1)."""
    session = _FakeSession()
    author = _user(7)

    proposal = await _service(session).create(author, _create_data())

    assert proposal.status == "DRAFT"
    assert proposal.version == 1
    assert proposal.author_id == 7
    assert proposal.slug == "renover-les-ecoles-publiques"


@pytest.mark.unit
async def test_create_records_initial_version_snapshot() -> None:
    """La création enregistre l'instantané de la version 1 (Exigences 3.4, 3.5)."""
    session = _FakeSession()

    proposal = await _service(session).create(_user(3), _create_data())

    assert len(session.versions) == 1
    version = session.versions[0]
    assert version.version == 1
    assert version.change_summary == "Création de la proposition"
    assert version.snapshot["status"] == "DRAFT"
    assert version.snapshot["title"] == proposal.title


@pytest.mark.unit
async def test_create_suffixes_slug_on_collision() -> None:
    """Un slug déjà pris est suffixé pour rester unique (Exigence 3.3)."""
    session = _FakeSession(existing_slugs={"renover-les-ecoles-publiques"})

    proposal = await _service(session).create(_user(1), _create_data())

    assert proposal.slug != "renover-les-ecoles-publiques"
    assert proposal.slug.startswith("renover-les-ecoles-publiques-")


# --------------------------------------------------------------------------- #
# Nouvelle version (Exigences 3.4, 3.5, 4.5)                                   #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_update_increments_version_with_change_summary() -> None:
    """``update`` incrémente la version et journalise ``change_summary`` (Exigences 3.4, 4.5)."""
    session = _FakeSession()
    author = _user(5)
    service = _service(session)
    proposal = await service.create(author, _create_data())

    updated = await service.update(
        author,
        proposal.id,
        ProposalUpdate(title="Rénover et isoler les écoles", change_summary="Ajout isolation"),
    )

    assert updated.version == 2
    assert updated.title == "Rénover et isoler les écoles"
    # Une version initiale + une version de modification, sans écrasement (Exigence 3.5).
    assert len(session.versions) == 2
    last = session.versions[-1]
    assert last.version == 2
    assert last.change_summary == "Ajout isolation"
    assert last.snapshot["title"] == "Rénover et isoler les écoles"


@pytest.mark.unit
async def test_update_preserves_previous_version_snapshot() -> None:
    """La version antérieure reste intacte après modification (Exigence 3.5)."""
    session = _FakeSession()
    author = _user(5)
    service = _service(session)
    proposal = await service.create(author, _create_data())
    original_title = proposal.title

    await service.update(
        author,
        proposal.id,
        ProposalUpdate(title="Nouveau titre suffisamment long", change_summary="MAJ"),
    )

    first_version = session.versions[0]
    assert first_version.version == 1
    assert first_version.snapshot["title"] == original_title


@pytest.mark.unit
async def test_update_missing_proposal_raises_not_found() -> None:
    """Modifier une Proposition inexistante lève ``ProposalNotFoundError`` (Exigence 31.2)."""
    session = _FakeSession()

    with pytest.raises(ProposalNotFoundError):
        await _service(session).update(_user(1), 999, ProposalUpdate(change_summary="x"))


# --------------------------------------------------------------------------- #
# Archivage (Exigence 4.6)                                                     #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_archive_sets_status_archived_and_keeps_history() -> None:
    """``archive`` passe le statut à ``ARCHIVED`` en conservant l'historique (Exigence 4.6)."""
    session = _FakeSession()
    author = _user(9)
    service = _service(session)
    proposal = await service.create(author, _create_data())
    versions_before = len(session.versions)

    archived = await service.archive(author, proposal.id)

    assert archived.status == "ARCHIVED"
    # L'historique n'est pas modifié par l'archivage.
    assert len(session.versions) == versions_before


@pytest.mark.unit
async def test_archive_missing_proposal_raises_not_found() -> None:
    """Archiver une Proposition inexistante lève ``ProposalNotFoundError`` (Exigence 31.2)."""
    session = _FakeSession()

    with pytest.raises(ProposalNotFoundError):
        await _service(session).archive(_user(1), 123)


# --------------------------------------------------------------------------- #
# Contrôle d'accès (Exigence 4.7)                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_update_by_non_author_non_admin_is_forbidden() -> None:
    """Un tiers ne peut pas modifier la Proposition (Exigence 4.7)."""
    session = _FakeSession()
    author = _user(1)
    intruder = _user(2)
    service = _service(session)
    proposal = await service.create(author, _create_data())

    with pytest.raises(ProposalPermissionError) as excinfo:
        await service.update(
            intruder, proposal.id, ProposalUpdate(title="Tentative non autorisée", change_summary="x")
        )
    assert str(excinfo.value) == FORBIDDEN_ERROR
    # La Proposition n'a pas changé de version.
    assert proposal.version == 1


@pytest.mark.unit
async def test_archive_by_non_author_non_admin_is_forbidden() -> None:
    """Un tiers ne peut pas archiver la Proposition (Exigence 4.7)."""
    session = _FakeSession()
    author = _user(1)
    intruder = _user(2)
    service = _service(session)
    proposal = await service.create(author, _create_data())

    with pytest.raises(ProposalPermissionError):
        await service.archive(intruder, proposal.id)
    assert proposal.status == "DRAFT"


@pytest.mark.unit
async def test_admin_can_update_any_proposal() -> None:
    """Un Administrateur peut modifier la Proposition d'un autre auteur (Exigence 4.7)."""
    session = _FakeSession()
    author = _user(1)
    admin = _user(42, is_admin=True)
    service = _service(session)
    proposal = await service.create(author, _create_data())

    updated = await service.update(
        admin, proposal.id, ProposalUpdate(title="Modifié par un admin", change_summary="admin")
    )

    assert updated.version == 2
    assert session.versions[-1].edited_by == 42


@pytest.mark.unit
async def test_admin_can_archive_any_proposal() -> None:
    """Un Administrateur peut archiver la Proposition d'un autre auteur (Exigence 4.7)."""
    session = _FakeSession()
    author = _user(1)
    admin = _user(42, is_admin=True)
    service = _service(session)
    proposal = await service.create(author, _create_data())

    archived = await service.archive(admin, proposal.id)

    assert archived.status == "ARCHIVED"
