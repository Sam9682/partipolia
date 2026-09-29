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
from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user_optional
from app.api.v1.chat import get_rag_pipeline
from app.core.database import get_session
from app.models.user import User
from app.rag.pipeline import RagPipeline
from app.schemas.auth import UserPublic
from app.schemas.legal_problem import AmendmentInput, ExplainInput
from app.schemas.mandate import CommitmentPublic, IndicatorPublic
from app.schemas.team import TeamPublic
from app.schemas.theme import ThemePublic
from app.services.audit_service import AuditService
from app.services.legal_analysis_service import LegalAnalysisService
from app.services.legal_reform_service import (
    ContributionLengthError,
    LegalProblemNotFoundError,
    LegalReformService,
)
from app.services.mandate_service import MandateService
from app.services.privacy_service import PrivacyService
from app.services.program_service import ProgramNotFoundError, ProgramService
from app.services.proposal_service import ProposalService
from app.services.statistics_service import StatisticsService
from app.services.team_service import TeamService
from app.services.theme_service import ThemeService

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
#   * ``/reparer-la-loi`` — page SSR « Réparer la loi » (liste des
#     Problèmes_Juridiques), ajoutée par la tâche 11.1 ;
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
    {"label": "Réparer la loi", "href": "/reparer-la-loi"},
    {"label": "Statistiques", "href": "/statistiques"},
    {"label": "Équipes", "href": "/equipes"},
    {"label": "Mandat", "href": "/mandat"},
    {"label": "Assistant", "href": "/assistant"},
    {"label": "API", "href": "/docs"},
    {"label": "Connexion", "href": "/login"},
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


def get_legal_reform_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LegalReformService:
    """Dépendance fournissant un :class:`LegalReformService` lié à la session de requête."""
    return LegalReformService(session)


def get_legal_analysis_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    pipeline: Annotated[RagPipeline, Depends(get_rag_pipeline)],
) -> LegalAnalysisService:
    """Dépendance fournissant un :class:`LegalAnalysisService` lié à la requête.

    Assemble le Service **exactement** comme le routeur API
    (:func:`app.api.v1.legal_problems.get_legal_analysis_service`) : réutilise le
    :class:`RagPipeline` du chemin de chat (:func:`app.api.v1.chat.get_rag_pipeline`)
    et l'associe à l':class:`AuditService` de la session courante. Sur le chemin SSR,
    le Service est utilisé en **lecture seule** : seules les analyses **matérialisées**
    par le Worker_Celery sont restituées, jamais générées en ligne (Exigences 7.1,
    7.11, 12.6, 16.6).
    """
    return LegalAnalysisService(session, pipeline, AuditService(session))


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
    commitments = [CommitmentPublic.model_validate(row).model_dump() for row in commitment_rows]
    indicators = [IndicatorPublic.model_validate(row).model_dump() for row in indicator_rows]
    return templates.TemplateResponse(
        request,
        "mandat.html",
        ssr_context(commitments=commitments, indicators=indicators),
    )


# ---------------------------------------------------------------------------
# Pages SSR « Réparer la loi » (Exigences 1.7, 16.1, 16.6).
#
# Consultation **publique**, en lecture seule et **sans authentification**
# (Exigences 1.7, 16.6), à l'image des autres pages « index/liste ». Le HTML est
# rendu **entièrement côté serveur** (Jinja2/HTMX/Alpine/Tailwind — Exigence 16.1).
# Les vues délèguent au :class:`LegalReformService` (domaine) et, pour le détail,
# lisent les analyses **matérialisées** via le :class:`LegalAnalysisService` :
# jamais aucune génération IA en ligne dans le chemin HTTP (lecture seule —
# Exigences 12.6, 16.6). Chaque objet de Service est sérialisé en payload simple
# (dict) avant injection dans son gabarit, de sorte que le rendu ne dépende pas
# d'objets ORM.
# ---------------------------------------------------------------------------


@web_router.get(
    "/reparer-la-loi",
    response_class=HTMLResponse,
    name="reparer_la_loi",
    summary="Page « Réparer la loi » — liste des Problèmes_Juridiques (SSR, publique)",
)
async def reparer_la_loi_page(
    request: Request,
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
) -> HTMLResponse:
    """Rend la page ``/reparer-la-loi`` : liste des Problèmes_Juridiques (Exigences 1.7, 16.1).

    Consultation **publique**, en lecture seule (aucune authentification —
    Exigences 1.7, 16.6) : délègue à :meth:`LegalReformService.list_problems`
    (première page, taille 20, avec ``total``). Chaque Problème_Juridique est
    sérialisé en payload simple (titre, résumé, nombre de citoyens concernés,
    textes de loi concernés, jurisprudence, Niveau_De_Complexité) avant rendu.
    """
    page = await service.list_problems(page=1, limit=20)
    problems = [item.model_dump() for item in page.items]
    return templates.TemplateResponse(
        request,
        "reparer_la_loi.html",
        ssr_context(problems=problems, total=page.total),
    )


@web_router.get(
    "/reparer-la-loi/{slug}",
    response_class=HTMLResponse,
    name="reparer_la_loi_detail",
    summary="Page « Réparer la loi » — détail d'un Problème_Juridique (SSR, publique)",
)
async def reparer_la_loi_detail_page(
    request: Request,
    slug: str,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> HTMLResponse:
    """Rend la page ``/reparer-la-loi/{slug}`` : détail d'un Problème_Juridique (Exigence 16.1).

    Consultation **publique**, en lecture seule (aucune authentification —
    Exigences 1.7, 16.6). Le Problème_Juridique est chargé par son ``slug`` via
    :meth:`LegalReformService.get_problem_by_slug` ; s'il est introuvable, la vue
    lève :class:`fastapi.HTTPException` ``404`` (sans détail interne), rendue par le
    modèle d'erreur normalisé de l'application.

    L'Analyse_Juridique du droit actuel et, pour chaque Réforme_Proposée, le bundle
    d'Agents_IA_Contradictoires (+ Conclusion_Technique) et la
    Simulation_De_Conséquences sont **lus depuis leur matérialisation** via le
    :class:`LegalAnalysisService` (jamais générés en ligne — Exigences 12.6, 16.6).
    Tous les objets de Service sont sérialisés en payloads simples (dict) avant
    injection dans le gabarit.

    L'état d'authentification est résolu en **lecture seule** via
    :func:`app.api.deps.get_current_user_optional` (ni l'absence ni l'invalidité du
    jeton ne provoquent d'erreur : ``None`` sinon). Il est injecté dans le contexte
    (``is_authenticated``) afin que le gabarit présente les actions par
    Réforme_Proposée (Approuver, Rejeter, Proposer un amendement, Demander une
    explication, Signaler un effet secondaire — Exigence 16.4) aux
    Utilisateurs_Authentifiés, et une **invitation à s'authentifier** (lien vers
    ``/login``) aux visiteurs anonymes, **sans déclencher** la moindre requête
    modifiant l'état (Exigence 8.10). La consultation de la page reste, elle,
    ouverte à tous (Exigence 16.6).
    """
    try:
        detail = await reform_service.get_problem_by_slug(slug)
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Problème juridique introuvable.",
        ) from exc

    # Analyse_Juridique du droit actuel (matérialisée) — clé sur l'identifiant du
    # Problème_Juridique (Exigences 3.1, 3.7, 12.6).
    legal_analysis = await analysis_service.get_legal_analysis(detail.id)

    # Pour chaque Réforme_Proposée : bundle d'agents (+ conclusion) et simulation,
    # lus depuis leur matérialisation, jamais générés en ligne (Exigences 7.11,
    # 12.6, 16.6).
    reforms: list[dict[str, Any]] = []
    for reform in detail.reforms:
        bundle = await analysis_service.get_reform_analyses(reform.reform_id)
        simulation = await analysis_service.get_simulation(reform.reform_id)
        reforms.append(
            {
                "reform": reform.model_dump(),
                "analyses": bundle.model_dump(),
                "simulation": simulation.model_dump(),
            }
        )

    return templates.TemplateResponse(
        request,
        "reparer_la_loi_detail.html",
        ssr_context(
            problem=detail.model_dump(),
            legal_analysis=legal_analysis.model_dump(),
            reforms=reforms,
            is_authenticated=current_user is not None,
        ),
    )


# ---------------------------------------------------------------------------
# Fragments HTMX d'actions par Réforme_Proposée (Exigences 8.10, 16.4).
#
# Ces points d'accès **SSR** vivent dans l'espace de noms ``/reparer-la-loi/...``
# (à distinguer des points d'accès JSON ``/api/v1/legal-problems/...`` de la
# tâche 8.3). Chaque action réussie renvoie un **fragment HTML** (partiel) que
# HTMX insère dans la zone de résultat de la Réforme : décomptes de votes mis à
# jour, confirmation d'amendement, texte d'explication cité, ou accusé de
# réception d'un signalement.
#
# Garde-fou d'authentification (Exigence 8.10) : l'état de connexion est résolu en
# **lecture seule** via :func:`get_current_user_optional`. Si le visiteur n'est
# PAS authentifié (``None``), le point d'accès retourne **immédiatement** un
# fragment d'invitation à s'authentifier **sans exécuter aucune mutation** — en
# particulier, aucun ``LegalReformService`` mutant n'est appelé, de sorte qu'un
# Vote_Citoyen existant reste rigoureusement inchangé.
#
# Les cinq actions consomment un corps **``application/x-www-form-urlencoded``**
# (défaut des soumissions HTMX), en cohérence avec le rendu SSR. La Réforme est
# rattachée au Problème_Juridique identifié par son ``slug`` : toute ressource
# absente est mappée sur ``404`` sans détail interne. Toute la logique métier
# (UPSERT du vote, versionnement de l'amendement, modération du signalement,
# récupération RAG de l'explication) est **déléguée** aux Services existants,
# sans duplication.
# ---------------------------------------------------------------------------


def _auth_required_fragment(request: Request) -> HTMLResponse:
    """Rend le fragment d'invitation à s'authentifier (Exigence 8.10).

    Retourné tel quel à un visiteur anonyme tentant une action, **sans** qu'aucune
    mutation d'état n'ait été effectuée (aucun Vote_Citoyen existant modifié). Le
    statut HTTP reste ``200`` afin que HTMX insère le fragment dans la zone de
    résultat de la Réforme.
    """
    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_auth_required.html",
        ssr_context(),
    )


def _action_error_fragment(request: Request, message: str) -> HTMLResponse:
    """Rend le fragment d'erreur d'action (contenu invalide), sans mutation.

    Utilisé lorsque le Service refuse une contribution (contenu vide/trop long) :
    le message reste générique, sans détail technique interne (Exigence 16.7).
    """
    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_action_error.html",
        ssr_context(message=message),
    )


async def _resolve_problem_id_or_404(reform_service: LegalReformService, slug: str) -> int:
    """Résout l'identifiant d'un Problème_Juridique depuis son ``slug`` (⇒ 404 sinon).

    Les Services d'action sont indexés par ``problem_id`` ; les pages SSR sont, elles,
    adressées par ``slug``. On convertit donc le ``slug`` en identifiant via
    :meth:`LegalReformService.get_problem_by_slug`, en mappant l'absence sur un
    ``404`` normalisé (sans détail interne).
    """
    try:
        detail = await reform_service.get_problem_by_slug(slug)
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Problème juridique introuvable.",
        ) from exc
    return detail.id


async def _cast_reform_vote_fragment(
    request: Request,
    reform_service: LegalReformService,
    current_user: UserPublic | None,
    slug: str,
    reform_id: int,
    value: int,
    message: str,
) -> HTMLResponse:
    """Fabrique commune Approuver/Rejeter : UPSERT du Vote puis fragment de décomptes.

    Garde-fou d'authentification (Exigence 8.10) : un visiteur anonyme reçoit le
    fragment d'invitation **avant** toute mutation. Sinon, le Vote_Citoyen est
    enregistré (UPSERT, délégué à ``vote_service`` via
    :meth:`LegalReformService.cast_vote`) et les décomptes à jour sont rendus
    (participation = support + oppose — Exigence 9.1).
    """
    if current_user is None:
        return _auth_required_fragment(request)

    problem_id = await _resolve_problem_id_or_404(reform_service, slug)
    # ``get_current_user_optional`` fournit un ``UserPublic`` ; les Services du
    # domaine acceptent un ``User`` (ils n'en exploitent que l'``id``). Le
    # transtypage local reflète ce contrat sans dupliquer la logique de vote.
    try:
        counts = await reform_service.cast_vote(
            cast(User, current_user), problem_id, reform_id, value
        )
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réforme introuvable.",
        ) from exc

    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_vote_counts.html",
        ssr_context(votes=counts.model_dump(), message=message),
    )


@web_router.post(
    "/reparer-la-loi/{slug}/reforms/{reform_id}/approve",
    response_class=HTMLResponse,
    name="reparer_la_loi_approve",
    include_in_schema=False,
    summary="Fragment HTMX — Approuver une Réforme_Proposée (Vote +1)",
)
async def reparer_la_loi_approve(
    request: Request,
    slug: str,
    reform_id: int,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> HTMLResponse:
    """Action « Approuver » : Vote_Citoyen ``+1`` (Exigences 8.1, 8.10, 16.4).

    Renvoie le fragment des décomptes mis à jour pour un Utilisateur authentifié,
    ou une invitation à s'authentifier — **sans altération d'état** — pour un
    visiteur anonyme (Exigence 8.10).
    """
    return await _cast_reform_vote_fragment(
        request,
        reform_service,
        current_user,
        slug,
        reform_id,
        value=1,
        message="Vote enregistré : vous approuvez cette réforme.",
    )


@web_router.post(
    "/reparer-la-loi/{slug}/reforms/{reform_id}/reject",
    response_class=HTMLResponse,
    name="reparer_la_loi_reject",
    include_in_schema=False,
    summary="Fragment HTMX — Rejeter une Réforme_Proposée (Vote -1)",
)
async def reparer_la_loi_reject(
    request: Request,
    slug: str,
    reform_id: int,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> HTMLResponse:
    """Action « Rejeter » : Vote_Citoyen ``-1`` (Exigences 8.2, 8.10, 16.4).

    Renvoie le fragment des décomptes mis à jour pour un Utilisateur authentifié,
    ou une invitation à s'authentifier — **sans altération d'état** — pour un
    visiteur anonyme (Exigence 8.10).
    """
    return await _cast_reform_vote_fragment(
        request,
        reform_service,
        current_user,
        slug,
        reform_id,
        value=-1,
        message="Vote enregistré : vous rejetez cette réforme.",
    )


@web_router.post(
    "/reparer-la-loi/{slug}/reforms/{reform_id}/amendments",
    response_class=HTMLResponse,
    name="reparer_la_loi_amendment",
    include_in_schema=False,
    summary="Fragment HTMX — Proposer un amendement à une Réforme_Proposée",
)
async def reparer_la_loi_amendment(
    request: Request,
    slug: str,
    reform_id: int,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
    content: Annotated[str, Form()] = "",
    change_summary: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Action « Proposer un amendement » (Exigences 8.7, 10.1–10.3, 8.10, 16.4).

    Un visiteur anonyme reçoit l'invitation à s'authentifier **sans** qu'aucune
    Version_De_Proposition ne soit créée (Exigence 8.10). Sinon, l'Amendement est
    délégué à ``proposal_service`` (nouvelle :class:`ProposalVersion` append-only) et
    un fragment de confirmation portant le nouveau numéro de version est rendu. Un
    contenu vide ou de plus de 5000 caractères est refusé sans mutation (fragment
    d'erreur — Exigence 8.9).
    """
    if current_user is None:
        return _auth_required_fragment(request)

    problem_id = await _resolve_problem_id_or_404(reform_service, slug)
    try:
        version = await reform_service.propose_amendment(
            cast(User, current_user),
            problem_id,
            reform_id,
            AmendmentInput(content=content, change_summary=change_summary),
        )
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réforme introuvable.",
        ) from exc
    except (ValidationError, ContributionLengthError):
        return _action_error_fragment(
            request, "Le texte de l'amendement doit comporter entre 1 et 5000 caractères."
        )

    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_amendment_result.html",
        ssr_context(version=version.version, change_summary=version.change_summary or ""),
    )


@web_router.post(
    "/reparer-la-loi/{slug}/reforms/{reform_id}/explain",
    response_class=HTMLResponse,
    name="reparer_la_loi_explain",
    include_in_schema=False,
    summary="Fragment HTMX — Demander une explication sur une Réforme_Proposée",
)
async def reparer_la_loi_explain(
    request: Request,
    slug: str,
    reform_id: int,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
    question: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Action « Demander une explication » (Exigences 8.5, 8.6, 8.10, 16.4).

    Un visiteur anonyme reçoit l'invitation à s'authentifier **sans** qu'aucune
    requête RAG ne soit émise et **sans** altérer son Vote_Citoyen existant
    (Exigence 8.10). Sinon, la demande est déléguée à
    :meth:`LegalAnalysisService.explain` (récupération obligatoire avant génération,
    lecture seule côté domaine) : la réponse cite ses Sources, ou porte un message
    d'absence si aucune Source n'est associée (Exigence 8.6). Une question vide est
    refusée sans mutation (fragment d'erreur).
    """
    if current_user is None:
        return _auth_required_fragment(request)

    problem_id = await _resolve_problem_id_or_404(reform_service, slug)
    # La Réforme doit être rattachée au Problème_Juridique (⇒ 404 sinon), contrôle
    # en lecture seule réutilisant la vérification du Service (aucune mutation).
    try:
        await reform_service.reform_vote_counts(problem_id, reform_id)
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réforme introuvable.",
        ) from exc

    try:
        data = ExplainInput(question=question)
    except ValidationError:
        return _action_error_fragment(request, "Veuillez saisir une question.")

    explanation = await analysis_service.explain(
        cast(User, current_user), problem_id, reform_id, data.question
    )
    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_explanation.html",
        ssr_context(explanation=explanation.model_dump()),
    )


@web_router.post(
    "/reparer-la-loi/{slug}/reforms/{reform_id}/side-effects",
    response_class=HTMLResponse,
    name="reparer_la_loi_side_effect",
    include_in_schema=False,
    summary="Fragment HTMX — Signaler un effet secondaire d'une Réforme_Proposée",
)
async def reparer_la_loi_side_effect(
    request: Request,
    slug: str,
    reform_id: int,
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
    content: Annotated[str, Form()] = "",
) -> HTMLResponse:
    """Action « Signaler un effet secondaire » (Exigences 8.8, 8.9, 15.1, 8.10, 16.4).

    Un visiteur anonyme reçoit l'invitation à s'authentifier **sans** qu'aucun
    Signalement ne soit créé (Exigence 8.10). Sinon, le Signalement est soumis au
    Moteur_De_Modération via :meth:`LegalReformService.report_side_effect` et un
    accusé de réception reflétant la décision (``VISIBLE`` / ``PENDING``) est rendu.
    Un contenu vide ou de plus de 5000 caractères est refusé sans mutation (fragment
    d'erreur — Exigence 8.9).
    """
    if current_user is None:
        return _auth_required_fragment(request)

    problem_id = await _resolve_problem_id_or_404(reform_service, slug)
    try:
        report = await reform_service.report_side_effect(
            cast(User, current_user), problem_id, reform_id, content
        )
    except LegalProblemNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Réforme introuvable.",
        ) from exc
    except ContributionLengthError:
        return _action_error_fragment(
            request, "Le signalement doit comporter entre 1 et 5000 caractères."
        )

    return templates.TemplateResponse(
        request,
        "partials/reparer_la_loi_side_effect_result.html",
        ssr_context(report_status=report.status),
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


@web_router.get(
    "/login",
    response_class=HTMLResponse,
    name="login",
    summary="Page Connexion (SSR, publique)",
)
async def login_page(request: Request) -> HTMLResponse:
    """Rend la page ``/login`` : formulaire de connexion (Exigences 2.1, 2.2, 2.3).

    Point d'entrée de connexion **public** (aucune authentification requise pour
    afficher le formulaire), à l'image des autres vues SSR. Elle n'appelle aucun
    Service : le formulaire soumet les identifiants au backend existant
    ``POST /api/v1/auth/login`` (corps JSON ``{email, password}``), qui dépose les
    cookies HttpOnly de session puis l'UI redirige vers une page publique. Aucun
    fichier ``app/api/v1/*`` n'est modifié.
    """
    return templates.TemplateResponse(request, "login.html", ssr_context())


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
    return templates.TemplateResponse(request, "rgpd/mentions_legales.html", ssr_context())


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
