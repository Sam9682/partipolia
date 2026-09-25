"""Routeur des pages Web SSR (Exigences 24, 25, 28.3).

Rassemble sous un unique :class:`fastapi.APIRouter` les pages rendues côté
serveur avec Jinja2/HTMX/Alpine.js/Tailwind : fiche Proposition
``/propositions/{slug}``, page Programme ``/programme`` et pages RGPD
(``/mentions-legales``, ``/confidentialite``, ``/cookies``, ``/conditions``).
Toutes ces pages sont consultables **sans authentification** (Exigence 24.4).

Le moteur de templates partagé est exposé ici afin que les vues et les tests
puissent le réutiliser sans reconstruire la configuration Jinja2.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.privacy_service import PrivacyService
from app.services.program_service import ProgramNotFoundError, ProgramService

# Répertoire des gabarits Jinja2 (``app/templates``), résolu relativement à ce
# fichier pour rester correct quel que soit le répertoire de travail courant.
TEMPLATES_DIR: Path = Path(__file__).resolve().parent.parent / "templates"

# Moteur de templates partagé par les vues SSR. ``autoescape`` est activé par
# défaut par ``Jinja2Templates`` (protection XSS des pages rendues).
templates: Jinja2Templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Routeur racine des pages Web. Monté à la racine du site par ``app.main``.
web_router: APIRouter = APIRouter()


def get_program_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProgramService:
    """Dépendance fournissant un :class:`ProgramService` lié à la session de requête."""
    return ProgramService(session)


@web_router.get(
    "/",
    name="accueil",
    include_in_schema=False,
    summary="Racine du site — redirige vers la page Programme",
)
async def accueil() -> RedirectResponse:
    """Page d'accueil ``/`` : redirige vers la page publique ``/programme``.

    La racine du site ne disposait d'aucun gestionnaire, ce qui produisait une
    réponse ``404`` normalisée (``code`` ``NOT_FOUND``) lors de la consultation
    de ``https://partipolia.fr``. On redirige donc vers la page Programme, point
    d'entrée public consultable sans authentification (Exigence 24.4).
    """
    return RedirectResponse(url="/programme", status_code=307)


@web_router.get(
    "/programme",
    response_class=HTMLResponse,
    name="programme",
    summary="Page Programme (SSR, publique)",
)
async def programme(
    request: Request,
    service: Annotated[ProgramService, Depends(get_program_service)],
) -> HTMLResponse:
    """Rend la page Programme ``/programme`` (Exigences 24.2, 24.3, 24.4, 25.1).

    Affiche la liste des Thèmes et, pour chaque mesure incluse au Programme, le
    nombre de propositions, le nombre de votes, le soutien agrégé et les coûts,
    recettes et économies estimés (Exigence 24.2). Les hypothèses des agrégats
    financiers sont explicitées dans le gabarit (Exigence 24.3). La consultation
    ne requiert **aucune authentification** (Exigence 24.4).

    Si aucun Programme n'existe encore (seed initial absent), la page est rendue
    avec une vue vide plutôt que de renvoyer une erreur, la consultation restant
    toujours possible.
    """
    try:
        view = await service.build_public_program_view()
        program_payload = view.model_dump()
    except ProgramNotFoundError:
        program_payload = {
            "program_id": 0,
            "themes": [],
            "total_measure_count": 0,
            "total_vote_count": 0,
            "total_estimated_cost": None,
            "total_estimated_revenue": None,
            "total_estimated_savings": None,
        }

    return templates.TemplateResponse(
        request,
        "programme.html",
        {"program": program_payload},
    )


# ---------------------------------------------------------------------------
# Pages RGPD publiques (Exigence 28.3) — consultables sans authentification.
# ---------------------------------------------------------------------------


@web_router.get(
    "/mentions-legales",
    response_class=HTMLResponse,
    name="mentions_legales",
    summary="Page Mentions légales (SSR, publique)",
)
async def mentions_legales(request: Request) -> HTMLResponse:
    """Rend la page ``/mentions-legales`` (Exigence 28.3), sans authentification."""
    return templates.TemplateResponse(request, "rgpd/mentions_legales.html", {})


@web_router.get(
    "/confidentialite",
    response_class=HTMLResponse,
    name="confidentialite",
    summary="Page Politique de confidentialité (SSR, publique)",
)
async def confidentialite(request: Request) -> HTMLResponse:
    """Rend la page ``/confidentialite`` (Exigences 28.3, 28.5), sans authentification.

    Expose la politique de rétention explicite (comptes, Votes, journaux,
    conversations IA, signalements ; Exigence 28.5) et rappelle l'absence de
    collecte de données superflues et de profil politique individuel
    (Exigences 28.1, 28.2). Décrit les fonctions RGPD disponibles : export des
    données, suppression de compte et gestion du consentement (Exigence 28.4).
    """
    policy = PrivacyService.retention_policy()
    return templates.TemplateResponse(
        request,
        "rgpd/confidentialite.html",
        {"retention_rules": [rule.model_dump() for rule in policy.rules]},
    )


@web_router.get(
    "/cookies",
    response_class=HTMLResponse,
    name="cookies",
    summary="Page Cookies (SSR, publique)",
)
async def cookies(request: Request) -> HTMLResponse:
    """Rend la page ``/cookies`` (Exigence 28.3), sans authentification."""
    return templates.TemplateResponse(request, "rgpd/cookies.html", {})


@web_router.get(
    "/conditions",
    response_class=HTMLResponse,
    name="conditions",
    summary="Page Conditions d'utilisation (SSR, publique)",
)
async def conditions(request: Request) -> HTMLResponse:
    """Rend la page ``/conditions`` (Exigence 28.3), sans authentification."""
    return templates.TemplateResponse(request, "rgpd/conditions.html", {})
