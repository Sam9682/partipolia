"""Routeur des pages Web SSR (Exigences 24, 25, 28.3).

Rassemble sous un unique :class:`fastapi.APIRouter` les pages rendues côté
serveur avec Jinja2/HTMX/Alpine.js/Tailwind : fiche Proposition
``/propositions/{slug}``, page Programme ``/programme`` et pages RGPD
(``/mentions-legales``, ``/confidentialite``, ``/cookies``, ``/conditions``).
Toutes ces pages sont consultables **sans authentification** (Exigence 24.4).

Le moteur de templates partagé est exposé ici afin que les vues et les tests
puissent le réutiliser sans reconstruire la configuration Jinja2.

La navigation d'en-tête est pilotée par une source de vérité unique côté
serveur (:data:`NAV_ITEMS`), injectée dans le contexte de chaque page SSR via
l'aide partagée :func:`ssr_context` afin d'éviter toute duplication et de
garantir un menu identique et sans lien mort sur toutes les pages.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.services.mandate_service import MandateService
from app.services.privacy_service import PrivacyService
from app.services.program_service import ProgramNotFoundError, ProgramService
from app.services.proposal_service import ProposalService
from app.services.statistics_service import StatisticsService
from app.services.team_service import TeamService
from app.services.theme_service import ThemeService
from app.schemas.mandate import CommitmentPublic, IndicatorPublic
from app.schemas.proposal import ProposalSummary
from app.schemas.team import TeamPublic
from app.schemas.theme import ThemePublic

# Répertoire des gabarits Jinja2 (``app/templates``), résolu relativement à ce
# fichier pour rester correct quel que soit le répertoire de travail courant.
TEMPLATES_DIR: Path = Path(__file__).resolve().parent.parent / "templates"

# Moteur de templates partagé par les vues SSR. ``autoescape`` est activé par
# défaut par ``Jinja2Templates`` (protection XSS des pages rendues).
templates: Jinja2Templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Routeur racine des pages Web. Monté à la racine du site par ``app.main``.
web_router: APIRouter = APIRouter()


# ---------------------------------------------------------------------------
# Source de vérité de la navigation d'en-tête (Exigence 2.1 — spec bugfix
# ``navigation-and-styling-fix``).
#
# ``NAV_ITEMS`` est l'unique source de vérité des liens du menu d'en-tête rendu
# par ``base.html``. Chaque entrée est un dict ordonné ``{"label", "href"}``.
# Contrainte « aucun lien mort » : le menu ne référence QUE des cibles
# réellement résolvables — soit une page SSR montée par ce routeur, soit un
# point d'accès garanti par le framework :
#
#   * ``/programme``    — page SSR existante (Exigence 24.2) ;
#   * ``/themes``, ``/propositions``, ``/statistiques``, ``/equipes``,
#     ``/mandat`` — pages « index/liste » SSR ajoutées par les tâches 3.2/3.3 ;
#   * ``/assistant``    — page SSR de l'Assistant (chat), ajoutée en 3.2/3.3 ;
#   * ``/docs``         — documentation OpenAPI, servie d'office par FastAPI,
#     point d'accès unique vers les fonctions restant purement API.
#
# L'ordre de la liste fixe l'ordre d'affichage du menu.
# ---------------------------------------------------------------------------
NAV_ITEMS: list[dict[str, str]] = [
    {"label": "Programme", "href": "/programme"},
    {"label": "Thèmes", "href": "/themes"},
    {"label": "Propositions", "href": "/propositions"},
    {"label": "Statistiques", "href": "/statistiques"},
    {"label": "Équipes", "href": "/equipes"},
    {"label": "Mandat", "href": "/mandat"},
    {"label": "Assistant", "href": "/assistant"},
    {"label": "API", "href": "/docs"},
]


def ssr_context(**extra: Any) -> dict[str, Any]:
    """Construit le contexte partagé des gabarits SSR (Exigence 2.1).

    Injecte ``nav_items`` (la source de vérité :data:`NAV_ITEMS`) dans toute
    réponse de gabarit afin que le menu d'en-tête de ``base.html`` soit piloté
    par les données et identique sur chaque page, sans duplication. Les paires
    clé/valeur propres à une vue sont passées via ``**extra`` et fusionnées
    au contexte partagé.

    Une copie de la liste est fournie pour éviter toute mutation accidentelle
    de la source de vérité par un gabarit.
    """
    return {"nav_items": list(NAV_ITEMS), **extra}


def get_program_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProgramService:
    """Dépendance fournissant un :class:`ProgramService` lié à la session de requête."""
    return ProgramService(session)


def get_theme_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ThemeService:
    """Dépendance fournissant un :class:`ThemeService` lié à la session de requête."""
    return ThemeService(session)


def get_proposal_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ProposalService:
    """Dépendance fournissant un :class:`ProposalService` lié à la session de requête."""
    return ProposalService(session)


def get_statistics_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> StatisticsService:
    """Dépendance fournissant un :class:`StatisticsService` lié à la session de requête."""
    return StatisticsService(session)


def get_team_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> TeamService:
    """Dépendance fournissant un :class:`TeamService` lié à la session de requête."""
    return TeamService(session)


def get_mandate_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> MandateService:
    """Dépendance fournissant un :class:`MandateService` lié à la session de requête."""
    return MandateService(session)


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
        ssr_context(program=program_payload),
    )


# ---------------------------------------------------------------------------
# Pages « index/liste » SSR publiques (Exigence 2.1 — spec bugfix
# ``navigation-and-styling-fix``).
#
# Ces vues, en **lecture seule** et **sans authentification**, existent d'abord
# pour rendre chaque entrée de :data:`NAV_ITEMS` résolvable (aucun lien mort).
# Elles s'adossent aux Services existants (aucune logique métier dupliquée) et
# n'écrivent jamais. Aucun fichier ``app/api/v1/*`` n'est touché : les Services
# sont réutilisés directement via des dépendances FastAPI, à l'image de
# :func:`get_program_service` / la vue :func:`programme`.
#
# Chaque vue sérialise les objets du Service en payloads simples (dict) avant de
# les injecter dans son gabarit ``templates/{fonction}.html`` via
# :func:`ssr_context`, de sorte que le rendu ne dépende pas d'objets ORM.
# ---------------------------------------------------------------------------


@web_router.get(
    "/themes",
    response_class=HTMLResponse,
    name="themes",
    summary="Page Thèmes (SSR, publique)",
)
async def themes_page(
    request: Request,
    service: Annotated[ThemeService, Depends(get_theme_service)],
) -> HTMLResponse:
    """Rend la page ``/themes`` : liste du Référentiel_Thématique (Exigences 2.1, 2.2).

    Consultation **publique**, en lecture seule : délègue à
    :meth:`ThemeService.list_themes`. Si le référentiel n'est pas encore amorcé,
    la page est rendue avec une liste vide plutôt qu'une erreur.
    """
    rows = await service.list_themes()
    themes = [ThemePublic.model_validate(row).model_dump() for row in rows]
    return templates.TemplateResponse(
        request,
        "themes.html",
        ssr_context(themes=themes),
    )


@web_router.get(
    "/propositions",
    response_class=HTMLResponse,
    name="propositions",
    summary="Page Propositions (SSR, publique)",
)
async def propositions_page(
    request: Request,
    service: Annotated[ProposalService, Depends(get_proposal_service)],
) -> HTMLResponse:
    """Rend la page ``/propositions`` : liste paginée des Propositions (Exigences 2.1, 4.3).

    Consultation **publique**, en lecture seule : délègue à
    :meth:`ProposalService.list` (première page, tri « recent »). Le tri ne
    s'appuie jamais sur le nombre de votes (Exigence 7.3).
    """
    page = await service.list(page=1, limit=20)
    proposals = [item.model_dump() for item in page.items]
    return templates.TemplateResponse(
        request,
        "propositions.html",
        ssr_context(proposals=proposals, total=page.total),
    )


@web_router.get(
    "/statistiques",
    response_class=HTMLResponse,
    name="statistiques",
    summary="Page Statistiques (SSR, publique)",
)
async def statistiques_page(
    request: Request,
    service: Annotated[StatisticsService, Depends(get_statistics_service)],
) -> HTMLResponse:
    """Rend la page ``/statistiques`` : compteurs globaux anonymisés (Exigences 2.1, 22.1).

    Consultation **publique**, en lecture seule : délègue à
    :meth:`StatisticsService.global_counts`. Les compteurs sont agrégés et
    anonymisés (Exigences 22.2, 22.3).
    """
    stats = await service.global_counts()
    return templates.TemplateResponse(
        request,
        "statistiques.html",
        ssr_context(statistics=stats.model_dump()),
    )


@web_router.get(
    "/equipes",
    response_class=HTMLResponse,
    name="equipes",
    summary="Page Équipes (SSR, publique)",
)
async def equipes_page(
    request: Request,
    service: Annotated[TeamService, Depends(get_team_service)],
) -> HTMLResponse:
    """Rend la page ``/equipes`` : liste des Équipes et de leurs membres (Exigences 2.1, 20.1).

    Consultation **publique**, en lecture seule : délègue à
    :meth:`TeamService.list_teams`. Si aucune Équipe n'existe, la page est rendue
    avec une liste vide plutôt qu'une erreur.
    """
    rows = await service.list_teams()
    teams = [TeamPublic.model_validate(row).model_dump() for row in rows]
    return templates.TemplateResponse(
        request,
        "equipes.html",
        ssr_context(teams=teams),
    )


@web_router.get(
    "/mandat",
    response_class=HTMLResponse,
    name="mandat",
    summary="Page Mandat (SSR, publique)",
)
async def mandat_page(
    request: Request,
    service: Annotated[MandateService, Depends(get_mandate_service)],
) -> HTMLResponse:
    """Rend la page ``/mandat`` : Engagements et Indicateurs du mandat (Exigences 2.1, 21.1, 21.3).

    Consultation **publique**, en lecture seule : délègue à
    :meth:`MandateService.list_commitments` et
    :meth:`MandateService.list_indicators`. Si le suivi de mandat n'est pas
    amorcé, la page est rendue avec des listes vides plutôt qu'une erreur.
    """
    commitment_rows = await service.list_commitments()
    indicator_rows = await service.list_indicators()
    commitments = [
        CommitmentPublic.model_validate(row).model_dump() for row in commitment_rows
    ]
    indicators = [
        IndicatorPublic.model_validate(row).model_dump() for row in indicator_rows
    ]
    return templates.TemplateResponse(
        request,
        "mandat.html",
        ssr_context(commitments=commitments, indicators=indicators),
    )


@web_router.get(
    "/assistant",
    response_class=HTMLResponse,
    name="assistant",
    summary="Page Assistant (SSR, publique)",
)
async def assistant_page(request: Request) -> HTMLResponse:
    """Rend la page ``/assistant`` : point d'entrée de l'Assistant (chat) (Exigence 2.1).

    Page d'entrée **publique**, en lecture seule : elle n'appelle aucun Service
    (le chat lui-même est exposé sous ``/api/v1``) et sert uniquement à rendre le
    lien de navigation « Assistant » résolvable et utile.
    """
    return templates.TemplateResponse(request, "assistant.html", ssr_context())


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
    return templates.TemplateResponse(
        request, "rgpd/mentions_legales.html", ssr_context()
    )


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
        ssr_context(retention_rules=[rule.model_dump() for rule in policy.rules]),
    )


@web_router.get(
    "/cookies",
    response_class=HTMLResponse,
    name="cookies",
    summary="Page Cookies (SSR, publique)",
)
async def cookies(request: Request) -> HTMLResponse:
    """Rend la page ``/cookies`` (Exigence 28.3), sans authentification."""
    return templates.TemplateResponse(request, "rgpd/cookies.html", ssr_context())


@web_router.get(
    "/conditions",
    response_class=HTMLResponse,
    name="conditions",
    summary="Page Conditions d'utilisation (SSR, publique)",
)
async def conditions(request: Request) -> HTMLResponse:
    """Rend la page ``/conditions`` (Exigence 28.3), sans authentification."""
    return templates.TemplateResponse(request, "rgpd/conditions.html", ssr_context())
