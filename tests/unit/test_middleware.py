"""Tests de `app.core.middleware` (sécurité + gestion globale des exceptions).

Couvre les Exigences 26.2, 26.3, 26.6, 26.7, 31.2, 31.3, 31.4 :

* :class:`RequestIdMiddleware` — attribution/propagation de ``X-Request-ID`` ;
* :class:`SecurityHeadersMiddleware` — en-têtes de sécurité HTTP ;
* gestionnaire global d'exceptions — modèle d'erreur normalisé pour les codes
  400/401/403/404/409/429/500 ;
* CORS piloté par la configuration (câblage vérifié via ``configure_security``
  + ``CORSMiddleware`` comme dans ``app.main``).

Les tests exercent une application FastAPI minimale via ``TestClient`` afin
d'isoler le comportement transverse du reste de l'API. Ils sont ignorés proprement
si ``fastapi``/``httpx`` sont indisponibles (environnement hors-ligne réduit).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import FastAPI, HTTPException, status  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from app.core.logging import REQUEST_ID_HEADER  # noqa: E402
from app.core.middleware import (  # noqa: E402
    SecurityHeadersMiddleware,
    configure_security,
)

# En-têtes de sécurité attendus (Exigence 26.6).
_EXPECTED_SECURITY_HEADERS = (
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Content-Security-Policy",
)


class _Payload(BaseModel):
    """Corps requis pour exercer la validation Pydantic (400)."""

    name: str
    age: int


def _build_app() -> FastAPI:
    """Construit une application minimale câblée comme ``app.main``."""
    app = FastAPI()
    configure_security(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://allowed.example"],
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("détail interne sensible")

    @app.get("/missing")
    async def missing() -> None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable."
        )

    @app.get("/denied")
    async def denied() -> None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Action non autorisée."
        )

    @app.get("/unauth")
    async def unauth() -> None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentification requise.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.get("/conflict")
    async def conflict() -> None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Conflit d'état."
        )

    @app.get("/limited")
    async def limited() -> None:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Trop de requêtes.",
            headers={"Retry-After": "30"},
        )

    @app.post("/validate")
    async def validate(payload: _Payload) -> dict[str, str]:
        return {"name": payload.name}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_build_app(), raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# RequestIdMiddleware (Exigences 26.3, 26.6)                                   #
# --------------------------------------------------------------------------- #
def test_request_id_generated_when_absent(client: TestClient) -> None:
    response = client.get("/ok")
    assert response.status_code == 200
    request_id = response.headers.get(REQUEST_ID_HEADER)
    assert request_id
    assert len(request_id) >= 8


def test_incoming_request_id_is_propagated(client: TestClient) -> None:
    response = client.get("/ok", headers={REQUEST_ID_HEADER: "corr-42"})
    assert response.headers.get(REQUEST_ID_HEADER) == "corr-42"


def test_request_ids_differ_between_requests(client: TestClient) -> None:
    first = client.get("/ok").headers[REQUEST_ID_HEADER]
    second = client.get("/ok").headers[REQUEST_ID_HEADER]
    assert first != second


# --------------------------------------------------------------------------- #
# SecurityHeadersMiddleware (Exigence 26.6)                                    #
# --------------------------------------------------------------------------- #
def test_security_headers_present_on_success(client: TestClient) -> None:
    response = client.get("/ok")
    for header in _EXPECTED_SECURITY_HEADERS:
        assert header in response.headers, f"en-tête manquant : {header}"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_security_headers_present_on_error(client: TestClient) -> None:
    response = client.get("/missing")
    for header in _EXPECTED_SECURITY_HEADERS:
        assert header in response.headers


def test_security_headers_not_overwritten() -> None:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/custom")
    async def custom():  # type: ignore[no-untyped-def]
        from fastapi.responses import JSONResponse

        return JSONResponse({"ok": True}, headers={"X-Frame-Options": "SAMEORIGIN"})

    response = TestClient(app).get("/custom")
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"


# --------------------------------------------------------------------------- #
# Gestionnaire global d'exceptions — modèle normalisé (Exigences 26.7, 31.x)  #
# --------------------------------------------------------------------------- #
def _assert_error_shape(body: dict, *, code: str, request_id: str) -> None:
    assert set(body) == {"error"}
    error = body["error"]
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    assert error["request_id"] == request_id


def test_unhandled_exception_returns_neutral_500(client: TestClient) -> None:
    response = client.get("/boom")
    assert response.status_code == 500
    body = response.json()
    _assert_error_shape(
        body, code="INTERNAL_ERROR", request_id=response.headers[REQUEST_ID_HEADER]
    )
    # Aucune fuite du détail interne de l'exception (Exigence 26.7).
    assert "détail interne sensible" not in body["error"]["message"]


def test_not_found_maps_to_normalized_404(client: TestClient) -> None:
    response = client.get("/missing")
    assert response.status_code == 404
    _assert_error_shape(
        response.json(),
        code="NOT_FOUND",
        request_id=response.headers[REQUEST_ID_HEADER],
    )


def test_forbidden_maps_to_normalized_403(client: TestClient) -> None:
    response = client.get("/denied")
    assert response.status_code == 403
    _assert_error_shape(
        response.json(),
        code="FORBIDDEN",
        request_id=response.headers[REQUEST_ID_HEADER],
    )


def test_unauthenticated_maps_to_normalized_401(client: TestClient) -> None:
    response = client.get("/unauth")
    assert response.status_code == 401
    _assert_error_shape(
        response.json(),
        code="UNAUTHENTICATED",
        request_id=response.headers[REQUEST_ID_HEADER],
    )
    # L'en-tête d'authentification est préservé.
    assert response.headers.get("WWW-Authenticate") == "Bearer"


def test_conflict_maps_to_normalized_409(client: TestClient) -> None:
    response = client.get("/conflict")
    assert response.status_code == 409
    _assert_error_shape(
        response.json(),
        code="CONFLICT",
        request_id=response.headers[REQUEST_ID_HEADER],
    )


def test_rate_limited_maps_to_normalized_429_with_retry_after(
    client: TestClient,
) -> None:
    response = client.get("/limited")
    assert response.status_code == 429
    _assert_error_shape(
        response.json(),
        code="RATE_LIMITED",
        request_id=response.headers[REQUEST_ID_HEADER],
    )
    assert response.headers.get("Retry-After") == "30"


def test_validation_error_maps_to_400_with_fields(client: TestClient) -> None:
    response = client.post("/validate", json={"name": "x"})  # age manquant
    assert response.status_code == 400
    body = response.json()
    _assert_error_shape(
        body,
        code="VALIDATION_ERROR",
        request_id=response.headers[REQUEST_ID_HEADER],
    )
    fields = body["error"]["fields"]
    assert any(f["field"] == "age" for f in fields)


def test_error_request_id_matches_incoming(client: TestClient) -> None:
    response = client.get("/missing", headers={REQUEST_ID_HEADER: "trace-xyz"})
    assert response.json()["error"]["request_id"] == "trace-xyz"
    assert response.headers[REQUEST_ID_HEADER] == "trace-xyz"


# --------------------------------------------------------------------------- #
# CORS piloté par la configuration (Exigence 26.2)                            #
# --------------------------------------------------------------------------- #
def test_cors_allows_configured_origin(client: TestClient) -> None:
    response = client.get("/ok", headers={"Origin": "http://allowed.example"})
    assert response.headers.get("access-control-allow-origin") == "http://allowed.example"


def test_cors_preflight_returns_allowed_methods(client: TestClient) -> None:
    response = client.options(
        "/ok",
        headers={
            "Origin": "http://allowed.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert "access-control-allow-methods" in response.headers
