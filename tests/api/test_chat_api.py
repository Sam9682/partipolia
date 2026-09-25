"""Tests d'API du chat RAG (Exigences 13.1, 14.7, 30.2 ; tâche 7.9).

Ces tests exercent le point d'accès ``POST /api/v1/chat`` de
``app/api/v1/chat.py`` via un ``fastapi.testclient.TestClient`` en **remplaçant**
uniquement ce qui touche l'extérieur :

* :func:`app.api.v1.chat.get_rag_pipeline` → un ``_FakeRagPipeline`` qui reproduit
  le contrat ``answer(message, proposal_id, user=...)`` en renvoyant une
  :class:`~app.schemas.chat.ChatResponse` prédéfinie, **sans** base de données ni
  fournisseur d'IA réel ;
* :func:`app.api.deps.get_current_user` → un Utilisateur authentifié fixe (ou
  absent pour vérifier le ``401``).

Le vrai code de routage, de validation Pydantic et de journalisation RAG est
exercé tel quel ; seule la brique RAG (recherche + génération) est simulée
(providers IA isolés — Exigences 16.4, Property 7/10).

Couverture :

* ``POST /chat`` renvoie ``{answer, sources[], confidence}`` (Exigence 13.1) ;
* accès **protégé** : sans authentification ⇒ ``401`` ;
* corps invalide (message vide) ⇒ ``422`` (Exigences 4.2/31.4 appliqués au chat) ;
* chaque réponse est journalisée avec user_id, modèle et retrieved_documents
  (Exigences 14.7, 30.2).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.api.v1.chat import get_rag_pipeline, router  # noqa: E402
from app.schemas.auth import UserPublic  # noqa: E402
from app.schemas.chat import ChatResponse, ChatSource  # noqa: E402

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _FakeRagPipeline:
    """Doublure du ``RagPipeline`` : renvoie une réponse fixe et enregistre l'appel."""

    def __init__(self, response: ChatResponse) -> None:
        self._response = response
        self.calls: list[tuple[str, int | None, object]] = []

    async def answer(
        self, message: str, proposal_id: int | None, user: object = None, **_kw: object
    ) -> ChatResponse:
        self.calls.append((message, proposal_id, user))
        return self._response


def _user(user_id: int = 10) -> UserPublic:
    return UserPublic(
        id=user_id,
        email=f"user{user_id}@example.org",
        display_name=f"User {user_id}",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_client(
    pipeline: _FakeRagPipeline, *, current_user: UserPublic | None
) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/chat")
    app.dependency_overrides[get_rag_pipeline] = lambda: pipeline
    if current_user is not None:
        app.dependency_overrides[get_current_user] = lambda: current_user
    return TestClient(app, raise_server_exceptions=True)


def _grounded_response() -> ChatResponse:
    return ChatResponse(
        answer="Le budget est de 3 milliards d'euros [1].",
        sources=[
            ChatSource(number=1, chunk_id=5, content="budget de 3 milliards", source_id=100, document_id=200)
        ],
        confidence=0.9,
    )


def test_chat_returns_answer_sources_confidence() -> None:
    """POST /chat renvoie {answer, sources[], confidence} (Exigence 13.1)."""
    pipeline = _FakeRagPipeline(_grounded_response())
    client = _build_client(pipeline, current_user=_user())

    resp = client.post("/api/v1/chat", json={"message": "Quel est le budget ?", "proposal_id": 7})

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "Le budget est de 3 milliards d'euros [1]."
    assert body["confidence"] == 0.9
    assert len(body["sources"]) == 1
    assert body["sources"][0]["number"] == 1
    assert body["sources"][0]["document_id"] == 200
    # La question et son rattachement sont bien transmis au pipeline.
    assert pipeline.calls[0][0] == "Quel est le budget ?"
    assert pipeline.calls[0][1] == 7


def test_chat_response_contract_exact_shape() -> None:
    """Le corps 200 expose **exactement** {answer, sources[], confidence} (Exigence 13.1).

    Vérifie le contrat de forme : clés de premier niveau attendues, ``sources`` est
    une liste de citations numérotées bien formées et ``confidence`` ∈ [0, 1].
    """
    multi_source = ChatResponse(
        answer="Deux mesures sont prévues [1][2].",
        sources=[
            ChatSource(number=1, chunk_id=5, content="mesure A", source_id=100, document_id=200),
            ChatSource(number=2, chunk_id=8, content="mesure B", source_id=101, document_id=201),
        ],
        confidence=0.75,
    )
    pipeline = _FakeRagPipeline(multi_source)
    client = _build_client(pipeline, current_user=_user())

    resp = client.post("/api/v1/chat", json={"message": "Quelles mesures ?"})

    assert resp.status_code == 200
    body = resp.json()
    # Contrat de premier niveau : ni plus, ni moins que {answer, sources, confidence}.
    assert set(body.keys()) == {"answer", "sources", "confidence"}
    assert isinstance(body["answer"], str)
    assert isinstance(body["confidence"], float)
    assert 0.0 <= body["confidence"] <= 1.0
    # Citations numérotées ordonnées et bien formées.
    assert [s["number"] for s in body["sources"]] == [1, 2]
    for source in body["sources"]:
        assert set(source.keys()) == {
            "number",
            "chunk_id",
            "content",
            "source_id",
            "document_id",
        }


def test_chat_requires_authentication() -> None:
    """Sans authentification, POST /chat renvoie 401 (endpoint protégé)."""
    pipeline = _FakeRagPipeline(_grounded_response())
    client = _build_client(pipeline, current_user=None)

    resp = client.post("/api/v1/chat", json={"message": "Question ?"})

    assert resp.status_code == 401
    # Le pipeline n'est jamais invoqué en l'absence d'authentification.
    assert pipeline.calls == []


def test_chat_empty_message_returns_422() -> None:
    """Un message vide ⇒ 422 (validation Pydantic, Exigence 31.4)."""
    pipeline = _FakeRagPipeline(_grounded_response())
    client = _build_client(pipeline, current_user=_user())

    resp = client.post("/api/v1/chat", json={"message": "   "})

    assert resp.status_code == 422


def test_chat_insufficient_information_response() -> None:
    """Sans passage pertinent : réponse d'indisponibilité, sources vides (13.8, 14.6)."""
    insufficient = ChatResponse(
        answer="Information insuffisante : aucune source pertinente.",
        sources=[],
        confidence=0.0,
    )
    pipeline = _FakeRagPipeline(insufficient)
    client = _build_client(pipeline, current_user=_user())

    resp = client.post("/api/v1/chat", json={"message": "Question obscure ?"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["sources"] == []
    assert body["confidence"] == 0.0


def test_chat_logs_rag_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chaque réponse RAG est journalisée avec user_id/modèle/documents (14.7, 30.2)."""
    from app.api.v1 import chat as chat_module

    events: list[tuple[str, dict[str, object]]] = []

    class _RecordingLogger:
        def info(self, event: str, **fields: object) -> None:
            events.append((event, fields))

    # Remplace le logger structuré du module par une doublure enregistrant les appels,
    # afin d'observer le contenu journalisé indépendamment du backend de logs.
    monkeypatch.setattr(chat_module, "_log", _RecordingLogger())
    monkeypatch.setattr(chat_module, "get_request_id", lambda: "req-xyz")

    pipeline = _FakeRagPipeline(_grounded_response())
    client = _build_client(pipeline, current_user=_user(42))

    resp = client.post("/api/v1/chat", json={"message": "Budget ?", "proposal_id": 7})

    assert resp.status_code == 200
    assert len(events) == 1
    event, fields = events[0]
    assert event == "rag_response"
    # Retrouvabilité de la requête IA (Exigence 30.2).
    assert fields["request_id"] == "req-xyz"
    assert fields["user_id"] == 42
    assert fields["model"]  # modèle de génération configuré
    assert fields["retrieved_documents"] == [200]
    assert "timestamp" in fields


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
