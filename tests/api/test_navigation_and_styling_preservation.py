"""Tests de préservation — Comportement inchangé hors conditions de bug.

Spec bugfix : ``.kiro/specs/navigation-and-styling-fix`` (tâche 2).

**Property 2: Preservation** — Comportement inchangé hors conditions de bug.

Méthodologie **observation-first** : on exécute d'abord le code NON corrigé,
on relève les sorties réelles (statuts, marqueurs de contenu, ``href`` du pied
de page, chargement du CDN, réponses API), puis on **affirme** ces sorties. Sur
le code NON corrigé ces tests PASSENT : ils capturent la ligne de base que la
correction (tâche 3) devra préserver.

On encode ``NOT isNavBugCondition(X) AND NOT isStylingBugCondition(X)`` des
*Preservation Requirements* de design.md :

* ``GET /programme`` rend le contenu du Programme (thèmes, mesures, votes,
  agrégats financiers) sans authentification (Exigence 3.1).
* ``GET /mentions-legales``, ``/confidentialite``, ``/cookies``, ``/conditions``
  sont rendues sans authentification (Exigence 3.2).
* Les quatre liens du pied de page pointent vers leurs pages respectives
  (Exigence 3.3).
* Quand le CDN Tailwind est référencé, le ``<head>`` conserve le chargement CDN
  (`cdn.tailwindcss.com`) intact (Exigence 3.4).
* Un échantillon représentatif d'endpoints ``/api/v1/*`` renvoie des réponses
  identiques (statut / forme) (Exigence 3.5).

Portée : strictement la couche SSR/présentation et l'observation de la surface
API existante. Aucun fichier ``app/api/v1/*`` ni source applicative n'est
modifié — cette tâche n'écrit que des tests.

Les propriétés sont formulées en test basé sur les propriétés (Hypothesis) là
où c'est pertinent : génération sur l'ensemble des chemins SSR publics et sur un
échantillon d'endpoints ``/api/v1/*``, pour une garantie « pour toute entrée non
buggy » plus forte.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.v1 import statistics as statistics_api
from app.api.v1 import themes as themes_api
from app.api.v1 import privacy as privacy_api
from app.schemas.program import (
    ProgramMeasure,
    ProgramPageView,
    ProgramThemeGroup,
)
from app.schemas.statistics import GlobalStatistics
from app.schemas.theme import ThemePublic
from app.web import router as web

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Répertoire des fichiers statiques réels, monté comme dans ``app/main.py``.
_STATIC_DIR = web.TEMPLATES_DIR.parent / "static"

# URL du CDN Tailwind attendu dans le ``<head>`` de ``base.html`` (Exigence 3.4).
_TAILWIND_CDN = "cdn.tailwindcss.com"

# Chemins des pages RGPD publiques (Exigence 3.2).
_RGPD_PATHS = ["/mentions-legales", "/confidentialite", "/cookies", "/conditions"]

# Liens attendus dans le pied de page (label → href) (Exigence 3.3).
_FOOTER_LINKS = {
    "Mentions légales": "/mentions-legales",
    "Confidentialité": "/confidentialite",
    "Cookies": "/cookies",
    "Conditions": "/conditions",
}


# --------------------------------------------------------------------------- #
# Doublures de Services (sans base de données), alignées sur les conventions   #
# de tests/api/test_programme_web.py et tests/api/test_sources_api.py.         #
# --------------------------------------------------------------------------- #
class _FakeProgramService:
    """``ProgramService`` factice renvoyant une :class:`ProgramPageView` fixe."""

    def __init__(self, view: ProgramPageView) -> None:
        self._view = view

    async def build_public_program_view(self) -> ProgramPageView:
        return self._view


class _FakeThemeService:
    """``ThemeService`` factice : liste de Thèmes en mémoire (Exigence 2.2)."""

    def __init__(self, themes: list[object]) -> None:
        self._themes = themes

    async def list_themes(self) -> list[object]:
        return self._themes


class _FakeStatisticsService:
    """``StatisticsService`` factice renvoyant des compteurs globaux fixes."""

    def __init__(self, stats: GlobalStatistics) -> None:
        self._stats = stats

    async def global_counts(self) -> GlobalStatistics:
        return self._stats


class _ThemeRow:
    """Objet minimal exposant les attributs lus par :class:`ThemePublic`."""

    def __init__(self, id: int, slug: str, name: str) -> None:
        self.id = id
        self.slug = slug
        self.name = name


def _sample_program_view() -> ProgramPageView:
    """Vue Programme d'exemple : un Thème, deux mesures (dont une non estimée)."""
    measures = [
        ProgramMeasure(
            proposal_id=1,
            slug="transport-gratuit",
            title="Transport public gratuit",
            support_count=8,
            oppose_count=2,
            participation_count=11,
            support_rate=0.8,
            estimated_cost=1_200_000.0,
            estimated_revenue=None,
            estimated_savings=50_000.0,
        ),
        ProgramMeasure(
            proposal_id=2,
            slug="pistes-cyclables",
            title="Extension des pistes cyclables",
            support_count=3,
            oppose_count=3,
            participation_count=6,
            support_rate=0.5,
            estimated_cost=None,
            estimated_revenue=None,
            estimated_savings=None,
        ),
    ]
    theme = ProgramThemeGroup(
        theme_id=10,
        slug="mobilite",
        name="Mobilité",
        measure_count=2,
        proposal_count=2,
        vote_count=17,
        support_count=11,
        oppose_count=5,
        support_rate=11 / 16,
        estimated_cost=1_200_000.0,
        estimated_revenue=None,
        estimated_savings=50_000.0,
        measures=measures,
    )
    return ProgramPageView(
        program_id=1,
        themes=[theme],
        total_measure_count=2,
        total_vote_count=17,
        total_estimated_cost=1_200_000.0,
        total_estimated_revenue=None,
        total_estimated_savings=50_000.0,
    )


# --------------------------------------------------------------------------- #
# Application de test : routeur SSR + montage /static + échantillon d'API.     #
# --------------------------------------------------------------------------- #
def _build_ssr_app() -> FastAPI:
    """Assemble une application montant le routeur SSR et ``/static`` (aucune auth).

    Reproduit fidèlement ``app/main.py`` pour les pages SSR : ``StaticFiles`` est
    monté sur ``/static`` et le service ``ProgramService`` est remplacé par une
    doublure afin d'exercer uniquement la couche de présentation, sans BD.
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web.web_router)
    app.dependency_overrides[web.get_program_service] = lambda: _FakeProgramService(
        _sample_program_view()
    )
    return app


def _ssr_client() -> TestClient:
    return TestClient(_build_ssr_app())


def _build_api_app() -> FastAPI:
    """Monte un échantillon représentatif d'endpoints publics ``/api/v1/*``.

    Sont montés (consultation publique, sans authentification) :

    * ``GET /api/v1/themes`` (Exigence 2.2) — doublure ``ThemeService`` ;
    * ``GET /api/v1/statistics`` (Exigence 22.1) — doublure ``StatisticsService`` ;
    * ``GET /api/v1/privacy/retention`` (Exigence 28.5) — statique, aucune
      doublure nécessaire.

    Aucun fichier ``app/api/v1/*`` n'est modifié : seules les fabriques de
    Services sont surchargées (convention de ``tests/api/test_sources_api.py``).
    """
    app = FastAPI()
    app.include_router(themes_api.router, prefix="/api/v1/themes", tags=["themes"])
    app.include_router(
        statistics_api.router, prefix="/api/v1/statistics", tags=["statistics"]
    )
    app.include_router(
        privacy_api.router, prefix="/api/v1/privacy", tags=["privacy"]
    )

    themes = [
        _ThemeRow(1, "mobilite", "Mobilité"),
        _ThemeRow(2, "logement", "Logement"),
    ]
    stats = GlobalStatistics(
        users=3, proposals=5, votes=17, comments=2, sources=4, themes=25
    )
    app.dependency_overrides[themes_api.get_theme_service] = (
        lambda: _FakeThemeService(themes)
    )
    app.dependency_overrides[statistics_api.get_statistics_service] = (
        lambda: _FakeStatisticsService(stats)
    )
    return app


def _api_client() -> TestClient:
    return TestClient(_build_api_app())


# --------------------------------------------------------------------------- #
# Parsers HTML : liens du pied de page (dernier <nav>) et contenu du <head>.   #
# --------------------------------------------------------------------------- #
class _FooterNavParser(HTMLParser):
    """Extrait ``(texte, href)`` des ``<a>`` du **dernier** ``<nav>`` (pied de page).

    ``base.html`` rend le ``<nav>`` d'en-tête d'abord, puis le ``<nav>`` du pied
    de page. On collecte donc tous les ``<nav>`` et on ne retient que le dernier.
    """

    def __init__(self) -> None:
        super().__init__()
        self._nav_depth = 0
        self._navs: list[list[tuple[str, str]]] = []
        self._current: list[tuple[str, str]] = []
        self._pending_href: str | None = None
        self._pending_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "nav":
            self._nav_depth += 1
            self._current = []
        elif tag == "a" and self._nav_depth > 0:
            self._pending_href = None
            self._pending_text = []
            for name, value in attrs:
                if name == "href" and value is not None:
                    self._pending_href = value

    def handle_data(self, data: str) -> None:
        if self._nav_depth > 0 and self._pending_href is not None:
            self._pending_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._nav_depth > 0 and self._pending_href is not None:
            text = "".join(self._pending_text).strip()
            self._current.append((text, self._pending_href))
            self._pending_href = None
            self._pending_text = []
        elif tag == "nav" and self._nav_depth > 0:
            self._nav_depth -= 1
            self._navs.append(self._current)
            self._current = []

    def footer_links(self) -> list[tuple[str, str]]:
        return self._navs[-1] if self._navs else []


def _footer_links(html: str) -> list[tuple[str, str]]:
    parser = _FooterNavParser()
    parser.feed(html)
    return parser.footer_links()


def _head_html(html: str) -> str:
    match = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


# --------------------------------------------------------------------------- #
# Exigence 3.1 — /programme reste rendu (contenu Programme) sans authentification.
# --------------------------------------------------------------------------- #
def test_programme_renders_program_content_without_auth() -> None:
    """``GET /programme`` reste public et rend le contenu du Programme (Exigence 3.1).

    Ligne de base observée sur le code NON corrigé : thèmes, mesures, décomptes
    de votes et agrégats financiers sont présents, sans authentification.

    _Requirements: 3.1_
    """
    response = _ssr_client().get("/programme")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")

    body = response.text
    # Thème et mesures (thèmes, mesures).
    assert "Mobilité" in body
    assert "Transport public gratuit" in body
    assert "Extension des pistes cyclables" in body
    # Votes / soutien agrégé.
    assert "17" in body
    assert "8 pour / 2 contre" in body
    # Agrégats financiers estimés et non estimés.
    assert "1 200 000 €" in body
    assert "Non estimé" in body


# --------------------------------------------------------------------------- #
# Exigence 3.2 — pages RGPD rendues sans authentification (propriété sur les 4).
# --------------------------------------------------------------------------- #
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=st.sampled_from(_RGPD_PATHS))
def test_rgpd_pages_render_without_auth(path: str) -> None:
    """Pour tout chemin RGPD, la page répond ``200`` en HTML sans auth (Exigence 3.2).

    Propriété sur l'ensemble des chemins RGPD publics ; ligne de base observée
    sur le code NON corrigé.

    _Requirements: 3.2_
    """
    response = _ssr_client().get(path)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# --------------------------------------------------------------------------- #
# Exigence 3.3 — les quatre liens du pied de page pointent vers leurs pages.    #
# --------------------------------------------------------------------------- #
def test_footer_links_point_to_rgpd_pages() -> None:
    """Les quatre liens du pied de page pointent vers leurs pages (Exigence 3.3).

    Ligne de base observée : le ``<nav>`` du pied de page de ``base.html`` liste
    Mentions légales, Confidentialité, Cookies et Conditions avec leurs ``href``
    respectifs. Ces ``href`` ne doivent pas changer après correction.

    _Requirements: 3.3_
    """
    html = _ssr_client().get("/programme").text
    links = _footer_links(html)
    href_by_label = {label: href for label, href in links}

    for label, expected_href in _FOOTER_LINKS.items():
        assert href_by_label.get(label) == expected_href, (
            f"Lien de pied de page « {label} » attendu vers {expected_href!r}, "
            f"trouvé {href_by_label.get(label)!r} (liens : {links!r})."
        )


@settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(item=st.sampled_from(list(_FOOTER_LINKS.items())))
def test_footer_link_targets_resolve_to_rgpd_pages(item: tuple[str, str]) -> None:
    """Chaque cible de lien du pied de page résout en page RGPD ``200`` (Exigence 3.3).

    Propriété : pour tout lien du pied de page, suivre son ``href`` mène à une
    page RGPD publique. Ligne de base observée sur le code NON corrigé.

    _Requirements: 3.3_
    """
    _label, href = item
    response = _ssr_client().get(href)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


# --------------------------------------------------------------------------- #
# Exigence 3.4 — le chargement du CDN Tailwind reste intact dans le <head>.    #
# --------------------------------------------------------------------------- #
def test_head_keeps_tailwind_cdn_loading_intact() -> None:
    """Le ``<head>`` conserve le chargement du CDN Tailwind (Exigence 3.4).

    Ligne de base observée : ``base.html`` référence ``cdn.tailwindcss.com`` via
    un ``<script>`` dans le ``<head>``. Le repli local (tâche 3) ne doit pas
    supprimer ce chargement CDN.

    _Requirements: 3.4_
    """
    head = _head_html(_ssr_client().get("/programme").text)

    assert _TAILWIND_CDN in head, (
        "Le <head> de base.html doit conserver le chargement du CDN Tailwind "
        f"({_TAILWIND_CDN!r}) — non trouvé dans : {head!r}"
    )
    # Le chargement se fait via un <script src="...cdn.tailwindcss.com...">.
    assert re.search(
        r'<script\b[^>]*\bsrc=["\'][^"\']*' + re.escape(_TAILWIND_CDN),
        head,
        re.IGNORECASE,
    ), "Le CDN Tailwind doit rester chargé via un <script src=...> dans le <head>."


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=st.sampled_from(["/programme", *_RGPD_PATHS]))
def test_tailwind_cdn_present_on_all_ssr_pages(path: str) -> None:
    """Pour tout chemin SSR public, le CDN Tailwind reste référencé (Exigence 3.4).

    Propriété sur l'ensemble des pages SSR héritant de ``base.html``.

    _Requirements: 3.4_
    """
    head = _head_html(_ssr_client().get(path).text)
    assert _TAILWIND_CDN in head


# --------------------------------------------------------------------------- #
# Exigence 3.5 — un échantillon d'endpoints /api/v1/* répond à l'identique.    #
# --------------------------------------------------------------------------- #
# Chaque cas : (chemin, statut attendu, validateur de forme sur le corps JSON).
def _validate_themes(body: object) -> None:
    assert isinstance(body, list)
    for item in body:
        # Forme d'un ThemePublic : {id, slug, name}.
        assert set(item.keys()) == {"id", "slug", "name"}
        ThemePublic.model_validate(item)


def _validate_statistics(body: object) -> None:
    assert isinstance(body, dict)
    # Forme de GlobalStatistics : compteurs entiers agrégés.
    assert set(body.keys()) == {
        "users",
        "proposals",
        "votes",
        "comments",
        "sources",
        "themes",
    }
    GlobalStatistics.model_validate(body)


def _validate_retention(body: object) -> None:
    assert isinstance(body, dict)
    assert "rules" in body
    assert isinstance(body["rules"], list)
    assert body["rules"], "La politique de rétention doit exposer des règles."


_API_ENDPOINTS: list[tuple[str, int, Callable[[object], None]]] = [
    ("/api/v1/themes", 200, _validate_themes),
    ("/api/v1/statistics", 200, _validate_statistics),
    ("/api/v1/privacy/retention", 200, _validate_retention),
]


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(case=st.sampled_from(_API_ENDPOINTS))
def test_api_v1_sample_endpoints_preserved(
    case: tuple[str, int, Callable[[object], None]],
) -> None:
    """Pour un échantillon d'endpoints ``/api/v1/*``, statut et forme sont préservés (Exigence 3.5).

    Propriété : chaque endpoint public échantillonné répond avec le statut et la
    forme de réponse observés sur le code NON corrigé. Le correctif ne touchant
    pas à ``app/api/v1/*``, ces réponses doivent rester identiques.

    _Requirements: 3.5_
    """
    path, expected_status, validate_shape = case
    response = _api_client().get(path)

    assert response.status_code == expected_status, (
        f"{path} : statut {response.status_code} (attendu {expected_status})."
    )
    assert response.headers["content-type"].startswith("application/json")
    validate_shape(response.json())


def test_api_v1_themes_baseline_content() -> None:
    """Ligne de base explicite de ``GET /api/v1/themes`` (statut + contenu) (Exigence 3.5).

    _Requirements: 3.5_
    """
    response = _api_client().get("/api/v1/themes")

    assert response.status_code == 200
    body = response.json()
    assert body == [
        {"id": 1, "slug": "mobilite", "name": "Mobilité"},
        {"id": 2, "slug": "logement", "name": "Logement"},
    ]


def test_api_v1_statistics_baseline_content() -> None:
    """Ligne de base explicite de ``GET /api/v1/statistics`` (statut + forme) (Exigence 3.5).

    _Requirements: 3.5_
    """
    response = _api_client().get("/api/v1/statistics")

    assert response.status_code == 200
    assert response.json() == {
        "users": 3,
        "proposals": 5,
        "votes": 17,
        "comments": 2,
        "sources": 4,
        "themes": 25,
    }
