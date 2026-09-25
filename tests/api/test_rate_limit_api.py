"""Tests d'API du câblage de la limitation de débit (Exigences 26.4, 26.5 ; tâche 10.2).

Ces tests vérifient que le ``RateLimiter`` est bien **branché** sur les points
d'accès sensibles via un ``fastapi.testclient.TestClient``, sans Redis réel :

* le client Redis partagé lu par ``app.core.rate_limit`` est remplacé par un
  ``_FakeRedis`` asynchrone en mémoire (``INCR``/``EXPIRE``) ;
* la configuration lue par le module est remplacée par des seuils bas et
  déterministes, afin de déclencher le ``429`` en peu de requêtes ;
* le gestionnaire global d'exceptions de ``app.core.middleware`` est enregistré
  pour valider que la réponse ``429`` suit le modèle d'erreur normalisé
  (``RATE_LIMITED``) **en conservant** l'en-tête ``Retry-After`` (Exigence 26.5).

On exerce le point d'accès ``POST /api/v1/chat`` (protection renforcée) : la brique
RAG et l'authentification sont simulées (voir ``tests/api/test_chat_api.py``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.deps import get_current_user  # noqa: E402
from app.api.v1.chat import get_rag_pipeline, router  # noqa: E402
from app.core import rate_limit as rate_limit_module  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.core.middleware import register_exception_handlers  # noqa: E402
from app.schemas.auth import UserPublic  # noqa: E402
from app.schemas.chat import ChatResponse  # noqa: E402

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)


@dataclass
class _FakeRedis:
    """Doublure asynchrone de Redis : ``INCR`` + ``EXPIRE`` en mémoire."""

    counts: dict[str, int] = field(default_factory=dict)

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        return True


class _FakePipeline:
    async def answer(
        self, message: str, proposal_id: int | None, user: object = None, **_kw: object
    ) -> ChatResponse:
        return ChatResponse(answer="ok", sources=[], confidence=0.0)


def _user() -> UserPublic:
    return UserPublic(
        id=10,
        email="user10@example.org",
        display_name="User 10",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _build_client(monkeypatch: pytest.MonkeyPatch, *, chat_max: int) -> TestClient:
    """Assemble une app chat avec limitation activée, Redis et config simulés."""
    # Seuils bas et déterministes : on part d'une config réelle puis on surcharge
    # uniquement les champs de rate limit lus par le module.
    settings = get_settings().model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_window_seconds": 60,
            "rate_limit_default_max": chat_max,
            "rate_limit_chat_max": chat_max,
        }
    )
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/chat")
    register_exception_handlers(app)
    app.dependency_overrides[get_rag_pipeline] = lambda: _FakePipeline()
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=True)


def test_chat_within_limit_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """En deçà du seuil renforcé du chat, les requêtes aboutissent (200)."""
    client = _build_client(monkeypatch, chat_max=3)

    for _ in range(3):
        resp = client.post("/api/v1/chat", json={"message": "Question ?"})
        assert resp.status_code == 200


def test_chat_over_limit_returns_429_with_retry_after(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Au-delà du seuil : 429 RATE_LIMITED + en-tête Retry-After (Exigences 26.4, 26.5)."""
    client = _build_client(monkeypatch, chat_max=2)

    # Les deux premières requêtes passent ; la troisième est limitée.
    assert client.post("/api/v1/chat", json={"message": "Q1"}).status_code == 200
    assert client.post("/api/v1/chat", json={"message": "Q2"}).status_code == 200

    resp = client.post("/api/v1/chat", json={"message": "Q3"})
    assert resp.status_code == 429
    # En-tête Retry-After préservé jusqu'au client (Exigence 26.5).
    assert "Retry-After" in resp.headers
    assert int(resp.headers["Retry-After"]) >= 1
    # Modèle d'erreur normalisé : code applicatif RATE_LIMITED.
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"


def _build_client_split(
    monkeypatch: pytest.MonkeyPatch, *, default_max: int, chat_max: int
) -> TestClient:
    """Assemble une app chat avec seuils **distincts** défaut vs chat.

    Permet de prouver que ``POST /api/v1/chat`` applique bien le seuil **dédié**
    ``rate_limit_chat_max`` (protection renforcée — Exigence 26.5) et **non** le
    seuil par défaut ``rate_limit_default_max``.
    """
    settings = get_settings().model_copy(
        update={
            "rate_limit_enabled": True,
            "rate_limit_window_seconds": 60,
            "rate_limit_default_max": default_max,
            "rate_limit_chat_max": chat_max,
        }
    )
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/chat")
    register_exception_handlers(app)
    app.dependency_overrides[get_rag_pipeline] = lambda: _FakePipeline()
    app.dependency_overrides[get_current_user] = lambda: _user()
    return TestClient(app, raise_server_exceptions=True)


def test_chat_uses_reinforced_threshold_not_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le chat applique le seuil dédié (renforcé), pas le seuil par défaut (26.5).

    Avec un seuil par défaut large (10) mais un seuil chat bas (2), la troisième
    requête au chat est limitée : preuve que ``POST /api/v1/chat`` est protégé par
    ``rate_limit_chat_max`` et non par ``rate_limit_default_max``.
    """
    client = _build_client_split(monkeypatch, default_max=10, chat_max=2)

    assert client.post("/api/v1/chat", json={"message": "Q1"}).status_code == 200
    assert client.post("/api/v1/chat", json={"message": "Q2"}).status_code == 200
    # Toujours sous le seuil par défaut (10) mais au-delà du seuil chat (2) ⇒ 429.
    resp = client.post("/api/v1/chat", json={"message": "Q3"})
    assert resp.status_code == 429
    assert "Retry-After" in resp.headers
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


def test_rate_limit_disabled_does_not_block(monkeypatch: pytest.MonkeyPatch) -> None:
    """Avec la limitation désactivée, aucun blocage même au-delà du seuil nominal."""
    settings = get_settings().model_copy(
        update={"rate_limit_enabled": False, "rate_limit_chat_max": 1}
    )
    fake_redis = _FakeRedis()
    monkeypatch.setattr(rate_limit_module, "get_settings", lambda: settings)
    monkeypatch.setattr(rate_limit_module, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/chat")
    register_exception_handlers(app)
    app.dependency_overrides[get_rag_pipeline] = lambda: _FakePipeline()
    app.dependency_overrides[get_current_user] = lambda: _user()
    client = TestClient(app, raise_server_exceptions=True)

    for _ in range(5):
        assert client.post("/api/v1/chat", json={"message": "Q"}).status_code == 200
    # Aucun compteur n'a été touché puisque la limitation est court-circuitée.
    assert fake_redis.counts == {}


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
