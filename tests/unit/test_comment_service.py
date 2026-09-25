"""Tests unitaires du ``CommentService`` (Exigence 9).

Couvre la logique du Service sans base de données réelle, via une ``AsyncSession``
factice n'implémentant que les opérations utilisées (``get``, ``add``, ``flush``,
``refresh``, ``delete``, ``scalars``). Le vrai code du Service **et** du
Moteur_De_Modération est exercé (aucun mock de la logique) :

* ``create`` soumet systématiquement le Commentaire au Moteur_De_Modération
  (Exigences 9.1, 17.x) : un contenu conforme est publié ``VISIBLE`` (Exigence
  9.4), un contenu douteux est retenu en File_De_Modération (``PENDING``) sans
  publication (Exigence 17.3) ;
* ``create`` vérifie l'existence de la Proposition (``404``) et du Commentaire
  parent rattaché à la même Proposition (Exigence 9.2) ;
* ``list_thread`` reconstitue l'arborescence des fils via ``parent_id``
  (Exigence 9.2) ;
* ``update`` / ``delete`` réservent l'action à l'auteur ou à un Administrateur
  (``403`` sinon, Exigence 9.3).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.comment import Comment
from app.models.proposal import Proposal
from app.schemas.auth import UserPublic
from app.schemas.moderation import ModerationClassification
from app.services.comment_service import (
    CommentNotFoundError,
    CommentPermissionError,
    CommentService,
    ParentCommentNotFoundError,
    ProposalNotFoundError,
)


class _FakeScalarResult:
    """Résultat scalaire factice exposant ``all()``."""

    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeSession:
    """``AsyncSession`` factice avec un magasin d'objets en mémoire.

    Elle ne mocke pas la logique : elle attribue des identifiants à la volée
    (``flush``) et renvoie les objets stockés (``get``, ``scalars``), permettant
    au vrai code du Service et de la modération de s'exécuter hors base.
    """

    def __init__(self) -> None:
        self.proposals: dict[int, Proposal] = {}
        self.comments: dict[int, Comment] = {}
        self._audit_logs: list[object] = []
        self._next_comment_id = 1

    # --- API utilisée par le Service et la modération --------------------- #
    async def get(self, model: type, ident: int) -> object | None:
        if model is Proposal:
            return self.proposals.get(ident)
        if model is Comment:
            return self.comments.get(ident)
        return None

    def add(self, obj: object) -> None:
        if isinstance(obj, Comment):
            if obj.id is None:
                obj.id = self._next_comment_id
                self._next_comment_id += 1
            if obj.status is None:
                obj.status = "VISIBLE"
            if getattr(obj, "created_at", None) is None:
                obj.created_at = datetime.now(timezone.utc)
            self.comments[obj.id] = obj
        else:  # entrées d'audit (AuditLog) et autres
            self._audit_logs.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: object) -> None:
        return None

    async def delete(self, obj: object) -> None:
        if isinstance(obj, Comment) and obj.id in self.comments:
            del self.comments[obj.id]

    async def scalars(self, _statement: object) -> _FakeScalarResult:
        rows = sorted(self.comments.values(), key=lambda c: c.id)
        return _FakeScalarResult(rows)


def _user(user_id: int, *, is_admin: bool = False) -> UserPublic:
    now = datetime.now(timezone.utc)
    return UserPublic(
        id=user_id,
        email=f"user{user_id}@example.org",
        display_name=f"User {user_id}",
        is_active=True,
        is_verified=True,
        is_admin=is_admin,
        created_at=now,
        updated_at=now,
    )


def _seed_proposal(session: _FakeSession, proposal_id: int = 1) -> None:
    proposal = Proposal(id=proposal_id)
    session.proposals[proposal_id] = proposal


@pytest.mark.unit
async def test_create_conforme_comment_is_published_visible() -> None:
    """Un Commentaire conforme est publié VISIBLE (Exigences 9.1, 9.4, 17.2)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    submission = await service.create(_user(10), 1, "Une contribution utile.")

    assert submission.moderation.classification is ModerationClassification.CONFORME
    assert submission.moderation.published is True
    assert submission.comment.status == "VISIBLE"
    assert submission.comment.proposal_id == 1
    assert submission.comment.author_id == 10


@pytest.mark.unit
async def test_create_douteux_comment_is_held_pending() -> None:
    """Un Commentaire douteux est retenu en File_De_Modération (Exigence 17.3)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    submission = await service.create(_user(10), 1, "ceci est du spam évident")

    assert submission.moderation.classification is ModerationClassification.DOUTEUX
    assert submission.moderation.published is False
    assert submission.comment.status == "PENDING"


@pytest.mark.unit
async def test_create_on_missing_proposal_raises_not_found() -> None:
    """La création sur une Proposition inexistante lève ``ProposalNotFoundError``."""
    session = _FakeSession()
    service = CommentService(session)  # type: ignore[arg-type]

    with pytest.raises(ProposalNotFoundError):
        await service.create(_user(10), 999, "Bonjour.")


@pytest.mark.unit
async def test_create_reply_requires_parent_on_same_proposal() -> None:
    """Un ``parent_id`` inexistant ou d'une autre Proposition est refusé (Exigence 9.2)."""
    session = _FakeSession()
    _seed_proposal(session, 1)
    _seed_proposal(session, 2)
    service = CommentService(session)  # type: ignore[arg-type]

    # Parent inexistant.
    with pytest.raises(ParentCommentNotFoundError):
        await service.create(_user(10), 1, "Réponse.", parent_id=42)

    # Parent existant mais rattaché à une autre Proposition.
    parent = await service.create(_user(10), 2, "Racine sur P2.")
    with pytest.raises(ParentCommentNotFoundError):
        await service.create(_user(11), 1, "Réponse croisée.", parent_id=parent.comment.id)


@pytest.mark.unit
async def test_list_thread_builds_reply_tree() -> None:
    """``list_thread`` reconstitue l'arborescence via ``parent_id`` (Exigence 9.2)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    root = await service.create(_user(10), 1, "Racine.")
    reply = await service.create(
        _user(11), 1, "Réponse à la racine.", parent_id=root.comment.id
    )
    await service.create(
        _user(12), 1, "Réponse à la réponse.", parent_id=reply.comment.id
    )
    await service.create(_user(13), 1, "Autre racine.")

    thread = await service.list_thread(1)

    assert [node.id for node in thread] == [root.comment.id, 4]
    root_node = thread[0]
    assert [node.id for node in root_node.replies] == [reply.comment.id]
    assert [node.id for node in root_node.replies[0].replies] == [3]


@pytest.mark.unit
async def test_update_by_non_author_is_forbidden() -> None:
    """Un non-auteur non administrateur ne peut pas modifier (``403``, Exigence 9.3)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    created = await service.create(_user(10), 1, "Mon commentaire.")

    with pytest.raises(CommentPermissionError):
        await service.update(_user(99), created.comment.id, "Détourné.")


@pytest.mark.unit
async def test_update_by_author_and_admin_succeeds() -> None:
    """L'auteur et un Administrateur peuvent modifier (Exigence 9.3)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    created = await service.create(_user(10), 1, "Version 1.")

    by_author = await service.update(_user(10), created.comment.id, "Version 2.")
    assert by_author.content == "Version 2."

    by_admin = await service.update(
        _user(1, is_admin=True), created.comment.id, "Version 3."
    )
    assert by_admin.content == "Version 3."


@pytest.mark.unit
async def test_delete_by_non_author_is_forbidden_then_author_succeeds() -> None:
    """Suppression refusée au non-auteur, permise à l'auteur (Exigence 9.3)."""
    session = _FakeSession()
    _seed_proposal(session)
    service = CommentService(session)  # type: ignore[arg-type]

    created = await service.create(_user(10), 1, "À supprimer.")

    with pytest.raises(CommentPermissionError):
        await service.delete(_user(99), created.comment.id)

    await service.delete(_user(10), created.comment.id)
    with pytest.raises(CommentNotFoundError):
        await service.update(_user(10), created.comment.id, "N'existe plus.")
