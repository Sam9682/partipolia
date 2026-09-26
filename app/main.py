"""Point d'entrée de l'application FastAPI PARTIPOLAI (Exigences 27.1, 27.2, 33.1).

Ce module assemble le monolithe modulaire (Exigence 33.1) :

* configuration de la journalisation structurée au démarrage et libération
  propre des ressources (moteur base de données, client Redis) à l'arrêt, via un
  gestionnaire de cycle de vie ``lifespan`` ;
* montage de l'API REST versionnée sous ``/api/v1`` (routeur agrégateur) ;
* montage de la couche de pages Web SSR (Jinja2/HTMX) à la racine du site, ainsi
  que des fichiers statiques sous ``/static`` ;
* configuration du middleware CORS à partir de la configuration applicative ;
* points de contrôle d'exploitation hors ``/api/v1`` :

  - ``GET /health`` renvoie ``{"status": "ok"}`` (Exigence 27.1) — sonde de
    vivacité, sans dépendance externe ;
  - ``GET /ready`` vérifie la disponibilité de PostgreSQL **et** de Redis avant
    de répondre (Exigence 27.2) — sonde de disponibilité, renvoyant ``503`` si
    l'une des dépendances est indisponible.

Lancement en développement ::

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1.router import api_router
from app.core.config import settings
from app.core.database import SessionLocal, dispose_engine
from app.core.logging import configure_logging, get_logger
from app.core.middleware import (
    RequestIdMiddleware,
    SecurityHeadersMiddleware,
    register_exception_handlers,
)
from app.core.redis import close_redis, get_redis
from app.web.router import TEMPLATES_DIR, web_router

# Répertoire des fichiers statiques (``app/static``), voisin des gabarits.
STATIC_DIR = TEMPLATES_DIR.parent / "static"

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Gestionnaire de cycle de vie de l'application.

    Au démarrage : configure la journalisation structurée (JSON + request_id).
    À l'arrêt : libère le pool de connexions du moteur SQLAlchemy et ferme le
    client Redis, afin d'éviter les fuites de connexions (Exigence 33.1).
    """
    configure_logging()
    logger.info("application_startup", app_env=settings.app_env)
    try:
        yield
    finally:
        await dispose_engine()
        await close_redis()
        logger.info("application_shutdown")


app = FastAPI(
    title="PARTIPOLAI",
    version="0.1.0",
    description=(
        "Plateforme de parti politique virtuel — API REST /api/v1 et rendu Web SSR "
        "(monolithe modulaire FastAPI + PostgreSQL/pgvector + Redis + Celery + RAG)."
    ),
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Middleware de sécurité et gestion globale des exceptions (Exigences 26, 31)
#
# Starlette exécute les middleware du **dernier ajouté au premier** à l'entrée.
# On ajoute donc, dans l'ordre : en-têtes de sécurité, request_id, puis CORS —
# de sorte que CORS s'exécute en premier (réponses préflight/OPTIONS et en-têtes
# CORS), le request_id soit lié avant le traitement, et les en-têtes de sécurité
# soient posés sur la réponse sortante (Exigences 26.2, 26.3, 26.6).
# ---------------------------------------------------------------------------
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=settings.cors_allow_methods,
    allow_headers=settings.cors_allow_headers,
)

# Gestionnaire global d'exceptions produisant le modèle d'erreur normalisé
# (codes 400/401/403/404/409/429/500) corrélé par X-Request-ID (Exigence 26.7).
register_exception_handlers(app)


# ---------------------------------------------------------------------------
# Points de contrôle d'exploitation (hors /api/v1) — Exigences 27.1, 27.2
# ---------------------------------------------------------------------------
@app.get("/health", tags=["ops"], summary="Sonde de vivacité")
async def health() -> dict[str, str]:
    """Sonde de vivacité (liveness) — Exigence 27.1.

    Renvoie ``{"status": "ok"}`` sans contacter de dépendance externe : indique
    seulement que le processus applicatif répond.
    """
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """Sert l'icône du site pour les requêtes automatiques ``GET /favicon.ico``.

    Les navigateurs demandent ``/favicon.ico`` par défaut ; sans gestionnaire,
    cela produisait une réponse ``404`` bruitant les journaux d'accès. On renvoie
    l'icône SVG servie sous ``app/static/favicon.svg`` avec un en-tête de cache.
    """
    return FileResponse(
        STATIC_DIR / "favicon.svg",
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@app.get("/ready", tags=["ops"], summary="Sonde de disponibilité")
async def ready() -> Response:
    """Sonde de disponibilité (readiness) — Exigence 27.2.

    Vérifie que PostgreSQL (``SELECT 1``) et Redis (``PING``) sont joignables
    avant de déclarer l'application prête. Renvoie ``200`` avec le détail par
    dépendance si tout est disponible, ou ``503`` (Service Unavailable) sinon.
    """
    checks: dict[str, str] = {}
    all_ok = True

    # PostgreSQL — exécute un SELECT 1 trivial sur une session éphémère.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception:  # noqa: BLE001 — toute erreur = dépendance indisponible
        logger.warning("readiness_database_unavailable", exc_info=True)
        checks["database"] = "unavailable"
        all_ok = False

    # Redis — un simple PING confirme la connectivité du client partagé.
    try:
        await get_redis().ping()
        checks["redis"] = "ok"
    except Exception:  # noqa: BLE001 — toute erreur = dépendance indisponible
        logger.warning("readiness_redis_unavailable", exc_info=True)
        checks["redis"] = "unavailable"
        all_ok = False

    payload = {"status": "ok" if all_ok else "unavailable", "checks": checks}
    status_code = status.HTTP_200_OK if all_ok else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(content=payload, status_code=status_code)


# ---------------------------------------------------------------------------
# Montage des routeurs : API REST /api/v1 puis pages Web SSR à la racine
# ---------------------------------------------------------------------------
app.include_router(api_router, prefix="/api/v1")

# Fichiers statiques (CSS/JS/Tailwind, images) servis sous /static.
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Pages Web SSR (Jinja2/HTMX) montées à la racine du site (Exigence 24).
app.include_router(web_router)
