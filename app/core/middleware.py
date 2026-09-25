"""Middleware de sécurité et gestion globale des exceptions (Exigences 26, 31).

Ce module centralise le durcissement transverse de l'API et du Web SSR :

* :class:`RequestIdMiddleware` — attribue (ou propage) un identifiant de requête
  ``X-Request-ID`` à chaque requête, le lie au contexte de journalisation
  structurée (:func:`app.core.logging.bind_request_id`) et le renvoie dans
  l'en-tête de réponse (Exigences 26.3, 26.6, 30.1) ;
* :class:`SecurityHeadersMiddleware` — ajoute les en-têtes de sécurité HTTP
  ``X-Content-Type-Options``, ``X-Frame-Options``, ``Referrer-Policy`` et
  ``Content-Security-Policy`` à toutes les réponses (Exigences 26.6) ;
* le **gestionnaire global d'exceptions** (:func:`register_exception_handlers`)
  normalise toutes les erreurs en un corps JSON stable (Exigences 26.7, 31.2,
  31.4), corrélé par ``request_id`` et masquant les traces internes.

La configuration CORS reste pilotée par :class:`app.core.config.Settings` et est
câblée séparément dans :mod:`app.main` via ``CORSMiddleware`` de Starlette
(Exigence 26.2). :func:`configure_security` regroupe l'installation des
middleware et des gestionnaires pour un câblage unique côté application.

Modèle d'erreur normalisé (design.md § Error Handling) ::

    {
      "error": {
        "code": "VALIDATION_ERROR",
        "message": "Le corps de la requête est invalide.",
        "request_id": "…",
        "fields": [{"field": "email", "message": "format invalide"}]
      }
    }

Table de correspondance code HTTP → ``code`` applicatif :

===== ===================
 400   VALIDATION_ERROR
 401   UNAUTHENTICATED
 403   FORBIDDEN
 404   NOT_FOUND
 409   CONFLICT
 429   RATE_LIMITED
 500   INTERNAL_ERROR
===== ===================
"""

from __future__ import annotations

from typing import Any, Final

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    generate_request_id,
    get_logger,
)

__all__ = [
    "REQUEST_ID_STATE_ATTR",
    "RequestIdMiddleware",
    "SecurityHeadersMiddleware",
    "configure_security",
    "error_response",
    "register_exception_handlers",
]

logger = get_logger(__name__)

# Attribut de ``request.state`` portant le request_id résolu, afin que les
# gestionnaires d'exceptions et les endpoints puissent le récupérer.
REQUEST_ID_STATE_ATTR: Final = "request_id"

# Correspondance code HTTP → ``code`` applicatif du modèle d'erreur normalisé
# (design.md § Error Handling ; Exigences 26.7, 31.2, 31.3, 31.4).
_HTTP_STATUS_TO_ERROR_CODE: Final[dict[int, str]] = {
    status.HTTP_400_BAD_REQUEST: "VALIDATION_ERROR",
    status.HTTP_401_UNAUTHORIZED: "UNAUTHENTICATED",
    status.HTTP_403_FORBIDDEN: "FORBIDDEN",
    status.HTTP_404_NOT_FOUND: "NOT_FOUND",
    status.HTTP_409_CONFLICT: "CONFLICT",
    status.HTTP_429_TOO_MANY_REQUESTS: "RATE_LIMITED",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "INTERNAL_ERROR",
}

# Message neutre renvoyé pour toute erreur non prévue : aucune fuite de trace
# interne côté client (Exigence 26.7).
_INTERNAL_ERROR_MESSAGE: Final = "Une erreur interne est survenue."

# Politique de sécurité de contenu par défaut. Volontairement stricte : aucune
# ressource externe non déclarée. ``'unsafe-inline'`` est toléré pour les styles
# et scripts inline utilisés par le rendu SSR (HTMX/Alpine/Tailwind) tant qu'un
# nonce n'est pas mis en place.
_DEFAULT_CSP: Final = (
    "default-src 'self'; "
    "img-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

# En-têtes de sécurité HTTP appliqués à toutes les réponses (Exigence 26.6).
_SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": _DEFAULT_CSP,
}


def _error_code_for_status(status_code: int) -> str:
    """Retourne le ``code`` applicatif associé à un code HTTP.

    Un code HTTP hors table est classé ``INTERNAL_ERROR`` s'il est ≥ 500, sinon
    ``VALIDATION_ERROR`` (erreur cliente générique) pour rester dans le modèle.
    """
    code = _HTTP_STATUS_TO_ERROR_CODE.get(status_code)
    if code is not None:
        return code
    return "INTERNAL_ERROR" if status_code >= 500 else "VALIDATION_ERROR"


def error_response(
    *,
    status_code: int,
    message: str,
    request_id: str | None,
    fields: list[dict[str, str]] | None = None,
    headers: dict[str, str] | None = None,
    code: str | None = None,
) -> JSONResponse:
    """Construit une :class:`JSONResponse` conforme au modèle d'erreur normalisé.

    :param status_code: code HTTP de la réponse.
    :param message: message lisible destiné au client (jamais une trace interne).
    :param request_id: identifiant de requête corrélant l'erreur aux journaux.
    :param fields: liste optionnelle des champs en erreur (validation ⇒ 400).
    :param headers: en-têtes additionnels (par exemple ``Retry-After`` sur 429).
    :param code: ``code`` applicatif explicite ; déduit du code HTTP si absent.
    """
    error: dict[str, Any] = {
        "code": code or _error_code_for_status(status_code),
        "message": message,
        "request_id": request_id,
    }
    if fields is not None:
        error["fields"] = fields
    # Le gestionnaire global d'exceptions de Starlette pour un ``Exception`` non
    # gérée s'exécute **en dehors** de ``RequestIdMiddleware`` ; on renvoie donc
    # explicitement ``X-Request-ID`` sur toute réponse d'erreur pour garantir la
    # corrélation client ↔ journaux (Exigences 26.3, 26.6).
    response_headers = dict(headers) if headers else {}
    if request_id is not None:
        response_headers.setdefault(REQUEST_ID_HEADER, request_id)
    return JSONResponse(
        status_code=status_code,
        content={"error": error},
        headers=response_headers or None,
    )


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attribue et propage l'identifiant de requête ``X-Request-ID``.

    Si la requête entrante porte déjà un en-tête ``X-Request-ID`` (par exemple
    injecté par Nginx en bordure), il est réutilisé ; sinon un nouvel identifiant
    est généré. L'identifiant est :

    * lié au contexte de journalisation structurée pour toute la durée de la
      requête (Exigences 26.3, 30.1) ;
    * exposé sur ``request.state`` pour les gestionnaires d'exceptions ;
    * renvoyé dans l'en-tête ``X-Request-ID`` de la réponse (Exigence 26.6).
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER)
        request_id = incoming.strip() if incoming and incoming.strip() else None
        with bind_request_id(request_id) as rid:
            setattr(request.state, REQUEST_ID_STATE_ATTR, rid)
            response = await call_next(request)
            response.headers[REQUEST_ID_HEADER] = rid
            return response


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Ajoute les en-têtes de sécurité HTTP à toutes les réponses (Exigence 26.6).

    Les en-têtes déjà présents sur la réponse ne sont pas écrasés, ce qui permet
    à un endpoint de surcharger ponctuellement une valeur (par exemple une CSP
    spécifique à une page).
    """

    def __init__(self, app: Any, headers: dict[str, str] | None = None) -> None:
        super().__init__(app)
        self._headers = dict(headers) if headers is not None else dict(_SECURITY_HEADERS)

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        for name, value in self._headers.items():
            response.headers.setdefault(name, value)
        return response


def _request_id_of(request: Request) -> str | None:
    """Récupère le request_id lié à la requête, ou ``None`` si absent."""
    return getattr(request.state, REQUEST_ID_STATE_ATTR, None)


async def _http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Normalise les :class:`HTTPException` en modèle d'erreur stable.

    Le ``detail`` de l'exception fournit le message client ; les éventuels
    en-têtes (``WWW-Authenticate`` sur 401, ``Retry-After`` sur 429) sont
    conservés (Exigences 31.2, 31.3, 26.5).
    """
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return error_response(
        status_code=exc.status_code,
        message=detail,
        request_id=_request_id_of(request),
        headers=dict(exc.headers) if exc.headers else None,
    )


async def _validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Normalise les erreurs de validation Pydantic en ``400`` (Exigences 4.2, 31.4).

    Chaque erreur est réduite au champ fautif (dernier segment du ``loc``, en
    ignorant le préfixe ``body``/``query``/…) et à son message.
    """
    fields: list[dict[str, str]] = []
    for err in exc.errors():
        location = [str(part) for part in err.get("loc", ()) if part not in ("body",)]
        field = location[-1] if location else "<corps>"
        fields.append({"field": field, "message": str(err.get("msg", "invalide"))})
    return error_response(
        status_code=status.HTTP_400_BAD_REQUEST,
        message="Le corps de la requête est invalide.",
        request_id=_request_id_of(request),
        fields=fields,
    )


async def _unhandled_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Filet de sécurité pour toute exception non gérée (Exigence 26.7).

    L'erreur complète est consignée avec son ``request_id`` dans la journalisation
    structurée ; le client ne reçoit qu'un message neutre ``500`` sans trace
    interne (Exigences 26.3, 26.7, 30.1).
    """
    request_id = _request_id_of(request)
    logger.error(
        "unhandled_exception",
        request_id=request_id,
        path=request.url.path,
        method=request.method,
        exc_info=exc,
    )
    return error_response(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=_INTERNAL_ERROR_MESSAGE,
        request_id=request_id,
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Enregistre les gestionnaires d'exceptions normalisant le modèle d'erreur.

    Couvre :class:`HTTPException` (codes 401/403/404/409/429…), les erreurs de
    validation Pydantic (400) et toute exception non gérée (500), afin que
    l'ensemble des réponses d'erreur suivent le corps JSON stable (Exigence 26.7).
    """
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(RequestValidationError, _validation_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)


def configure_security(app: FastAPI) -> None:
    """Câble middleware de sécurité et gestionnaires d'exceptions sur l'application.

    Ordre d'ajout (Starlette exécute les middleware du dernier ajouté au premier
    en entrée) : :class:`SecurityHeadersMiddleware` puis :class:`RequestIdMiddleware`,
    de sorte que le request_id soit lié **avant** le traitement de la requête et
    disponible pour les en-têtes de sécurité et les journaux. Les gestionnaires
    d'exceptions sont enregistrés en complément (Exigences 26.3, 26.6, 26.7).

    La politique CORS (Exigence 26.2) est câblée séparément dans :mod:`app.main`
    via ``CORSMiddleware`` à partir de la configuration applicative.
    """
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(RequestIdMiddleware)
    register_exception_handlers(app)
