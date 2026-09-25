"""Tests d'API des Commentaires (Exigence 9, tâches 5.8 et 11.1).

Ces tests exercent la couche HTTP de ``app/api/v1/comments.py`` (câblage des deux
routeurs, codes de statut, garde d'authentification et sérialisation) **sans base
de données** : le :class:`~app.services.comment_service.CommentService` est
remplacé par une doublure qui reproduit son contrat public en mémoire
(création → modération, fils via ``parent_id``, modification/suppression avec
contrôle auteur/Administrateur). La vraie logique du Service est couverte par
ailleurs (``tests/unit``) ; on valide ici uniquement le comportement de l'API.

Le montage suit exactement celui de production (cf. ``app.api.v1.router``) :

* :data:`proposal_comments_router` est monté sous ``/api/v1/proposals`` et expose
  ``GET``/``POST /{proposal_id}/comments`` ;
* :data:`router` est monté sous ``/api/v1/comments`` et expose
  ``PUT``/``DELETE /{comment_id}``.

Couverture (Exigences 9.1, 9.2, 9.3, 9.4, 9.5, 17.x) :

* ``POST`` publie un Commentaire conforme (``VISIBLE``) → ``201`` ; un Commentaire
  douteux est retenu en File_De_Modération (``PENDING``, non publié) → ``201`` ;
  ``401`` si anonyme ; ``404`` si Proposition ou parent introuvable ; ``422`` si
  ``content`` vide ;
* ``GET`` reconstitue les fils via ``parent_id`` (arborescence) — consultation
  publique ;
* ``PUT``/``DELETE`` par l'auteur ou un Administrateur ; ``403`` pour un
  non-auteur ; ``401`` si anonyme ; ``404`` si absent.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.api.v1 import comments as comments_api  # noqa: E402
from app.schemas.auth import UserPublic  # noqa: E402
from app.schemas.comment import CommentNode, CommentPublic, CommentSubmission  # noqa: E402
from app.schemas.moderation import ModerationClassification, ModerationDecision  # noqa: E402
from app.services.comment_service import (  # noqa: E402
    CommentNotFoundError,
    CommentPermissionError,
    ParentCommentNotFoundError,
    ProposalNotFoundError,
)

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=UTC)

# Terme déclenchant la classification « douteux » de la doublure de modération.
_FLAGGED_TERM = "insulte"


class _CommentRow:
    """Commentaire minimal conservé en mémoire par la doublure de Service."""

    def __init__(
        self,
        *,
        comment_id: int,
        proposal_id: int,
        author_id: int,
        content: str,
        parent_id: int | None = None,
        status: str = "VISIBLE",
    ) -> None:
        self.id = comment_id
        self.proposal_id = proposal_id
        self.author_id = author_id
        self.parent_id = parent_id
        self.content = content
        self.status = status
        self.created_at = _NOW


class _FakeCommentService:
    """Doublure du ``CommentService`` : contrat public sans base de données.

    * ``create`` applique une modération triviale (le terme :data:`_FLAGGED_TERM`
      rend le Commentaire ``douteux`` ⇒ ``PENDING`` non publié, sinon ``conforme``
      ⇒ ``VISIBLE``), vérifie l'existence de la Proposition et du parent, et
      renvoie une :class:`CommentSubmission` (Exigences 9.1, 9.4, 17.x) ;
    * ``list_thread`` reconstruit l'arborescence via ``parent_id`` (Exigence 9.2) ;
    * ``update``/``delete`` appliquent le contrôle auteur/Administrateur
      (Exigence 9.3).
    """

    def __init__(
        self,
        *,
        known_proposals: set[int] | None = None,
    ) -> None:
        self.store: dict[int, _CommentRow] = {}
        self.known_proposals = known_proposals if known_proposals is not None else {7}
        self._next_id = 1

    async def create(
        self,
        user: UserPublic,
        proposal_id: int,
        content: str,
        parent_id: int | None = None,
        *,
        ip_hash: str | None = None,
    ) -> CommentSubmission:
        if proposal_id not in self.known_proposals:
            raise ProposalNotFoundError("Proposition introuvable.")
        if parent_id is not None:
            parent = self.store.get(parent_id)
            if parent is None or parent.proposal_id != proposal_id:
                raise ParentCommentNotFoundError("Commentaire parent introuvable.")

        flagged = _FLAGGED_TERM in content.lower()
        status = "PENDING" if flagged else "VISIBLE"
        row = _CommentRow(
            comment_id=self._next_id,
            proposal_id=proposal_id,
            author_id=user.id,
            content=content,
            parent_id=parent_id,
            status=status,
        )
        self.store[row.id] = row
        self._next_id += 1

        decision = ModerationDecision(
            comment_id=row.id,
            classification=(
                ModerationClassification.DOUTEUX
                if flagged
                else ModerationClassification.CONFORME
            ),
            status=status,
            published=not flagged,
            reason=_FLAGGED_TERM if flagged else None,
        )
        return CommentSubmission(
            comment=CommentPublic.model_validate(row, from_attributes=True),
            moderation=decision,
        )

    async def list_thread(self, proposal_id: int) -> list[CommentNode]:
        rows = [
            r for r in self.store.values() if r.proposal_id == proposal_id
        ]
        rows.sort(key=lambda r: r.id)
        nodes: dict[int, CommentNode] = {
            r.id: CommentNode(
                id=r.id,
                proposal_id=r.proposal_id,
                author_id=r.author_id,
                parent_id=r.parent_id,
                content=r.content,
                status=r.status,
                created_at=r.created_at,
                replies=[],
            )
            for r in rows
        }
        roots: list[CommentNode] = []
        for r in rows:
            node = nodes[r.id]
            if r.parent_id is not None and r.parent_id in nodes:
                nodes[r.parent_id].replies.append(node)
            else:
                roots.append(node)
        return roots

    async def update(
        self, actor: UserPublic, comment_id: int, content: str
    ) -> _CommentRow:
        row = self.store.get(comment_id)
        if row is None:
            raise CommentNotFoundError("Commentaire introuvable.")
        self._authorize(actor, row)
        row.content = content
        return row

    async def delete(self, actor: UserPublic, comment_id: int) -> None:
        row = self.store.get(comment_id)
        if row is None:
            raise CommentNotFoundError("Commentaire introuvable.")
        self._authorize(actor, row)
        del self.store[comment_id]

    @staticmethod
    def _authorize(actor: UserPublic, row: _CommentRow) -> None:
        if actor.is_admin or actor.id == row.author_id:
            return
        raise CommentPermissionError("Action réservée à l'auteur ou à un administrateur.")


def _user(user_id: int = 10, *, is_admin: bool = False) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"user{user_id}@example.org",
        display_name=f"User {user_id}",
        is_active=True,
        is_verified=True,
        is_admin=is_admin,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_client(
    service: _FakeCommentService,
    *,
    current_user: UserPublic | None,
) -> TestClient:
    """Assemble une app montant les deux routeurs comme en production."""
    app = FastAPI()
    app.include_router(
        comments_api.proposal_comments_router, prefix="/api/v1/proposals", tags=["comments"]
    )
    app.include_router(comments_api.router, prefix="/api/v1/comments", tags=["comments"])
    app.dependency_overrides[comments_api.get_comment_service] = lambda: service
    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app, raise_server_exceptions=True)


# --------------------------------------------------------------------------- #
# POST /api/v1/proposals/{id}/comments — création + modération                 #
# --------------------------------------------------------------------------- #
def test_create_comment_conforme_is_published_visible() -> None:
    """Un Commentaire conforme est publié VISIBLE → 201 (Exigences 9.1, 9.4)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user())

    resp = client.post(
        "/api/v1/proposals/7/comments", json={"content": "Un avis constructif"}
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["comment"]["status"] == "VISIBLE"
    assert body["comment"]["author_id"] == 10
    assert body["moderation"]["classification"] == "conforme"
    assert body["moderation"]["published"] is True


def test_create_comment_douteux_is_held_pending_not_published() -> None:
    """Un Commentaire douteux est retenu PENDING sans publication (Exigence 17.3)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user())

    resp = client.post(
        "/api/v1/proposals/7/comments",
        json={"content": f"Ceci est une {_FLAGGED_TERM}"},
    )

    assert resp.status_code == 201
    body = resp.json()
    assert body["comment"]["status"] == "PENDING"
    assert body["moderation"]["classification"] == "douteux"
    assert body["moderation"]["published"] is False
    assert body["moderation"]["reason"] == _FLAGGED_TERM


def test_create_comment_requires_authentication() -> None:
    """Sans authentification, POST renvoie 401 (Exigences 1.10, 9.5)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=None)

    resp = client.post("/api/v1/proposals/7/comments", json={"content": "Anonyme"})

    assert resp.status_code == 401


def test_create_comment_on_missing_proposal_returns_404() -> None:
    """POST sur une Proposition inexistante ⇒ 404 (Exigence 31.2)."""
    service = _FakeCommentService(known_proposals=set())
    client = _build_client(service, current_user=_user())

    resp = client.post("/api/v1/proposals/999/comments", json={"content": "Salut"})

    assert resp.status_code == 404


def test_create_reply_on_missing_parent_returns_404() -> None:
    """Un parent inexistant ⇒ 404 (Exigence 9.2)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user())

    resp = client.post(
        "/api/v1/proposals/7/comments",
        json={"content": "Réponse", "parent_id": 12345},
    )

    assert resp.status_code == 404


def test_create_comment_empty_content_returns_422() -> None:
    """Un ``content`` vide est rejeté par la validation (Exigence 4.2/31.4)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user())

    resp = client.post("/api/v1/proposals/7/comments", json={"content": ""})

    assert resp.status_code == 422
    fields = {tuple(err["loc"][-1:]) for err in resp.json()["detail"]}
    assert ("content",) in fields


# --------------------------------------------------------------------------- #
# GET /api/v1/proposals/{id}/comments — fils via parent_id                     #
# --------------------------------------------------------------------------- #
def test_list_thread_is_public_and_builds_parent_id_tree() -> None:
    """GET reconstitue l'arborescence via parent_id, sans authentification (Exigence 9.2)."""
    service = _FakeCommentService()
    author = _user()
    client = _build_client(service, current_user=author)

    # Racine, puis deux réponses rattachées à la racine, puis une sous-réponse.
    root = client.post(
        "/api/v1/proposals/7/comments", json={"content": "Racine"}
    ).json()["comment"]
    reply1 = client.post(
        "/api/v1/proposals/7/comments",
        json={"content": "Réponse 1", "parent_id": root["id"]},
    ).json()["comment"]
    client.post(
        "/api/v1/proposals/7/comments",
        json={"content": "Réponse 2", "parent_id": root["id"]},
    )
    client.post(
        "/api/v1/proposals/7/comments",
        json={"content": "Sous-réponse", "parent_id": reply1["id"]},
    )

    # Lecture publique (aucune dépendance d'auth requise pour GET).
    public = _build_client(service, current_user=None)
    resp = public.get("/api/v1/proposals/7/comments")

    assert resp.status_code == 200
    thread = resp.json()
    # Un seul fil racine.
    assert len(thread) == 1
    node = thread[0]
    assert node["parent_id"] is None
    assert node["content"] == "Racine"
    # Deux réponses directes, ordonnées chronologiquement.
    assert [r["content"] for r in node["replies"]] == ["Réponse 1", "Réponse 2"]
    # La première réponse porte elle-même une sous-réponse (arborescence récursive).
    assert [r["content"] for r in node["replies"][0]["replies"]] == ["Sous-réponse"]


# --------------------------------------------------------------------------- #
# PUT /api/v1/comments/{id} — modification (auteur/Administrateur)             #
# --------------------------------------------------------------------------- #
def test_update_comment_by_author_returns_200() -> None:
    """PUT par l'auteur applique la modification → 200 (Exigence 9.3)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="Avant"
    )
    client = _build_client(service, current_user=_user(10))

    resp = client.put("/api/v1/comments/1", json={"content": "Après"})

    assert resp.status_code == 200
    assert resp.json()["content"] == "Après"


def test_update_comment_by_non_author_returns_403() -> None:
    """PUT par un non-auteur non-admin ⇒ 403 (Exigence 9.3)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="Avant"
    )
    client = _build_client(service, current_user=_user(99))

    resp = client.put("/api/v1/comments/1", json={"content": "Pirate"})

    assert resp.status_code == 403


def test_update_missing_comment_returns_404() -> None:
    """PUT sur un Commentaire absent ⇒ 404 (Exigence 9.3)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user(10))

    resp = client.put("/api/v1/comments/404", json={"content": "Neuf"})

    assert resp.status_code == 404


def test_update_comment_requires_authentication() -> None:
    """PUT anonyme ⇒ 401 (Exigences 1.10, 9.5)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="Avant"
    )
    client = _build_client(service, current_user=None)

    resp = client.put("/api/v1/comments/1", json={"content": "Après"})

    assert resp.status_code == 401


# --------------------------------------------------------------------------- #
# DELETE /api/v1/comments/{id} — suppression (auteur/Administrateur)           #
# --------------------------------------------------------------------------- #
def test_delete_comment_by_author_returns_204() -> None:
    """DELETE par l'auteur ⇒ 204 (Exigence 9.3)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="À supprimer"
    )
    client = _build_client(service, current_user=_user(10))

    resp = client.delete("/api/v1/comments/1")

    assert resp.status_code == 204
    assert 1 not in service.store


def test_delete_comment_by_admin_returns_204() -> None:
    """Un Administrateur peut supprimer le Commentaire d'autrui (Exigence 9.3)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="À supprimer"
    )
    client = _build_client(service, current_user=_user(1, is_admin=True))

    resp = client.delete("/api/v1/comments/1")

    assert resp.status_code == 204


def test_delete_comment_by_non_author_returns_403() -> None:
    """DELETE par un non-auteur non-admin ⇒ 403 (Exigence 9.3)."""
    service = _FakeCommentService()
    service.store[1] = _CommentRow(
        comment_id=1, proposal_id=7, author_id=10, content="À supprimer"
    )
    client = _build_client(service, current_user=_user(99))

    resp = client.delete("/api/v1/comments/1")

    assert resp.status_code == 403


def test_delete_missing_comment_returns_404() -> None:
    """DELETE sur un Commentaire absent ⇒ 404 (Exigence 9.3)."""
    service = _FakeCommentService()
    client = _build_client(service, current_user=_user(10))

    resp = client.delete("/api/v1/comments/404")

    assert resp.status_code == 404
