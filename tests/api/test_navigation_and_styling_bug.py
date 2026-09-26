"""Tests d'exploration des conditions de bug — Navigation incomplète & style CDN-only.

Spec bugfix : ``.kiro/specs/navigation-and-styling-fix`` (tâche 1).

**Property 1: Bug Condition** — Navigation incomplète et style uniquement CDN.

Ces tests encodent le **comportement attendu (corrigé)** et DOIVENT donc
ÉCHOUER sur le code NON corrigé : leur échec **confirme** que les deux bugs
existent (c'est le résultat de succès attendu pour cette tâche d'exploration).
Une fois la correction implémentée (tâche 3), ces mêmes tests devront passer.

Portée : strictement la couche SSR/présentation (``app/web/router.py``,
``app/templates/base.html``, actif statique ``/static/css/app.css``). Aucun
fichier ``app/api/v1/*`` n'est touché.

Conditions de bug encodées (cf. design.md) :

* ``isNavBugCondition(X)`` : toute page rendue via ``base.html``. On rend
  ``GET /programme`` avec le ``TestClient`` FastAPI/Starlette, on parse le
  ``<nav>`` d'en-tête et on affirme que le nombre de liens de navigation est
  ``> 1``. Sur le code non corrigé, l'en-tête ne rend qu'un lien ``/programme``
  codé en dur → l'assertion ÉCHOUE.
* ``isStylingBugCondition(X)`` : page via ``base.html`` sans feuille de style
  locale. On affirme que ``GET /static/css/app.css`` renvoie ``200`` et que le
  ``<head>`` rendu contient un ``<link rel="stylesheet" href="/static/css/app.css">``
  local. Sur le code non corrigé, l'actif renvoie ``404`` et le ``<head>`` n'a
  aucun ``<link>`` local → les assertions ÉCHOUENT.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient

from app.schemas.program import ProgramPageView
from app.schemas.statistics import GlobalStatistics
from app.web import router as web

pytestmark = pytest.mark.api

# Répertoire des fichiers statiques réels de l'application (``app/static``),
# monté exactement comme dans ``app/main.py`` pour reproduire fidèlement le
# service de l'actif ``/static/css/app.css``.
_STATIC_DIR = web.TEMPLATES_DIR.parent / "static"

# Chemin de la feuille de style locale attendue après correction.
_LOCAL_CSS_PATH = "/static/css/app.css"


class _FakeProgramService:
    """``ProgramService`` factice renvoyant une :class:`ProgramPageView` vide.

    On ne teste ici que la couche de présentation (en-tête / chargement du
    style) ; le contenu du Programme n'importe pas.
    """

    async def build_public_program_view(self) -> ProgramPageView:
        return ProgramPageView(
            program_id=1,
            themes=[],
            total_measure_count=0,
            total_vote_count=0,
        )


# Doublures des Services adossant les pages « index/liste » SSR ajoutées par la
# correction (tâches 3.2/3.3). Elles renvoient des collections vides / des
# compteurs à zéro, à l'image des doublures de
# ``tests/api/test_navigation_and_styling_preservation.py`` : on ne teste ici
# que la **résolution** des routes (aucun lien mort), pas leur contenu, donc les
# vues doivent pouvoir rendre leur gabarit sans dépendre d'une base de données.
class _FakeThemeService:
    """``ThemeService`` factice : référentiel thématique vide."""

    async def list_themes(self) -> list[object]:
        return []


class _EmptyPage:
    """Page paginée vide exposant ``items`` et ``total`` (contrat lu par la vue)."""

    items: list[object] = []
    total: int = 0


class _FakeProposalService:
    """``ProposalService`` factice : première page de Propositions vide."""

    async def list(self, *, page: int = 1, limit: int = 20) -> _EmptyPage:
        return _EmptyPage()


class _FakeStatisticsService:
    """``StatisticsService`` factice : compteurs globaux à zéro."""

    async def global_counts(self) -> GlobalStatistics:
        return GlobalStatistics(
            users=0, proposals=0, votes=0, comments=0, sources=0, themes=0
        )


class _FakeTeamService:
    """``TeamService`` factice : aucune Équipe."""

    async def list_teams(self) -> list[object]:
        return []


class _FakeMandateService:
    """``MandateService`` factice : aucun Engagement ni Indicateur."""

    async def list_commitments(self) -> list[object]:
        return []

    async def list_indicators(self) -> list[object]:
        return []


def _build_app() -> FastAPI:
    """Application minimale : routeur SSR + montage ``/static`` (comme ``app.main``).

    On monte ``StaticFiles`` sur ``/static`` pointant vers le vrai ``app/static``
    afin que ``GET /static/css/app.css`` reflète l'état réel du dépôt (404 tant
    que l'actif n'existe pas).
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web.web_router)
    app.dependency_overrides[web.get_program_service] = _FakeProgramService
    # Doublures des Services adossant les nouvelles pages SSR : elles évitent tout
    # accès à la base de données (stubbé en erreur par ``conftest.py`` hors ligne)
    # afin que les routes de navigation nouvellement ajoutées résolvent bien.
    app.dependency_overrides[web.get_theme_service] = _FakeThemeService
    app.dependency_overrides[web.get_proposal_service] = _FakeProposalService
    app.dependency_overrides[web.get_statistics_service] = _FakeStatisticsService
    app.dependency_overrides[web.get_team_service] = _FakeTeamService
    app.dependency_overrides[web.get_mandate_service] = _FakeMandateService
    return app


def _client() -> TestClient:
    return TestClient(_build_app())


class _HeaderNavLinkParser(HTMLParser):
    """Extrait les ``href`` des ``<a>`` du **premier** ``<nav>`` rencontré.

    Le gabarit ``base.html`` place le ``<nav>`` d'en-tête avant le ``<nav>`` du
    pied de page ; on ne considère donc que le premier ``<nav>`` (l'en-tête).
    """

    def __init__(self) -> None:
        super().__init__()
        self._in_first_nav = False
        self._first_nav_done = False
        self.header_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "nav" and not self._first_nav_done:
            self._in_first_nav = True
        elif tag == "a" and self._in_first_nav:
            for name, value in attrs:
                if name == "href" and value is not None:
                    self.header_links.append(value)

    def handle_endtag(self, tag: str) -> None:
        if tag == "nav" and self._in_first_nav:
            self._in_first_nav = False
            self._first_nav_done = True


def _header_nav_links(html: str) -> list[str]:
    """Retourne la liste des ``href`` des liens du ``<nav>`` d'en-tête."""
    parser = _HeaderNavLinkParser()
    parser.feed(html)
    return parser.header_links


def _head_html(html: str) -> str:
    """Retourne le contenu du ``<head>`` du document (chaîne vide si absent)."""
    match = re.search(r"<head\b[^>]*>(.*?)</head>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


# ---------------------------------------------------------------------------
# isNavBugCondition — l'en-tête doit exposer plusieurs liens résolvables.
# ---------------------------------------------------------------------------
def test_header_nav_exposes_more_than_one_link() -> None:
    """Expected Behavior (Property 1) : ``headerNav(result).count > 1``.

    Sur le code NON corrigé, l'en-tête ne contient qu'un lien ``/programme`` :
    ce test ÉCHOUE, ce qui confirme le bug de navigation incomplète.

    _Requirements: 1.1 (targets Expected Behavior 2.1)_
    """
    html = _client().get("/programme").text
    links = _header_nav_links(html)

    assert len(links) > 1, (
        "Contre-exemple bug navigation : le <nav> d'en-tête de /programme "
        f"contient {len(links)} lien(s) {links!r} (attendu > 1)."
    )


def test_header_nav_links_point_to_main_functions() -> None:
    """L'en-tête doit mener aux fonctions principales, pas seulement à ``/programme``.

    Sur le code NON corrigé, seul ``/programme`` est présent : ce test ÉCHOUE.

    _Requirements: 1.1 (targets Expected Behavior 2.1)_
    """
    html = _client().get("/programme").text
    links = _header_nav_links(html)

    extra_targets = [href for href in links if href not in ("/programme", "/")]
    assert extra_targets, (
        "Contre-exemple bug navigation : aucun lien vers d'autres fonctions que "
        f"/programme dans l'en-tête (liens trouvés : {links!r})."
    )


# ---------------------------------------------------------------------------
# isStylingBugCondition — feuille de style locale servie et référencée.
# ---------------------------------------------------------------------------
def test_local_stylesheet_asset_is_served() -> None:
    """Expected Behavior (Property 1) : ``GET /static/css/app.css`` → ``200``.

    Sur le code NON corrigé, ``app/static`` ne contient que ``.gitkeep`` :
    l'actif renvoie ``404`` et ce test ÉCHOUE, confirmant l'absence de repli
    local (style uniquement via CDN).

    _Requirements: 1.2 (targets Expected Behavior 2.2)_
    """
    response = _client().get(_LOCAL_CSS_PATH)

    assert response.status_code == 200, (
        "Contre-exemple bug style : "
        f"GET {_LOCAL_CSS_PATH} renvoie {response.status_code} (attendu 200) — "
        "aucune feuille de style locale servie sous /static."
    )


def test_base_head_references_local_stylesheet() -> None:
    """Expected Behavior (Property 1) : le ``<head>`` contient un ``<link>`` local.

    Sur le code NON corrigé, ``base.html`` ne charge que des scripts CDN et
    aucun ``<link rel="stylesheet">`` local : ce test ÉCHOUE.

    _Requirements: 1.2, 1.3 (targets Expected Behavior 2.2, 2.3)_
    """
    html = _client().get("/programme").text
    head = _head_html(html)

    # Recherche d'un <link rel="stylesheet" ... href="/static/css/app.css"> local
    # (ordre des attributs indifférent).
    has_local_link = bool(
        re.search(
            r'<link\b[^>]*\brel=["\']stylesheet["\'][^>]*\bhref=["\']'
            + re.escape(_LOCAL_CSS_PATH),
            head,
            re.IGNORECASE,
        )
        or re.search(
            r'<link\b[^>]*\bhref=["\']'
            + re.escape(_LOCAL_CSS_PATH)
            + r'["\'][^>]*\brel=["\']stylesheet["\']',
            head,
            re.IGNORECASE,
        )
    )

    assert has_local_link, (
        "Contre-exemple bug style : le <head> de base.html ne contient aucun "
        f'<link rel="stylesheet" href="{_LOCAL_CSS_PATH}"> local (dépendance '
        "exclusive au CDN Tailwind)."
    )


# ---------------------------------------------------------------------------
# Cas limite — aucun lien mort : les cibles de navigation autres que /programme
# et RGPD résolvent désormais côté SSR.
#
# Ce test documentait initialement l'état NON corrigé (ces cibles renvoyaient
# ``404``, ce qui justifiait d'ajouter des pages index plutôt que des liens
# bruts). Sur le code CORRIGÉ (tâches 3.1–3.3), sa prémisse est inversée : les
# pages « index/liste » existent maintenant et le menu ne doit contenir aucun
# lien mort (design.md — Property 1 : « chacun pointant vers une route réellement
# résolvable »). Il vérifie donc désormais le comportement attendu : chacune de
# ces cibles résout (``200``) lorsque ses Services d'appui sont disponibles.
# ---------------------------------------------------------------------------
def test_previously_dead_nav_targets_now_resolve() -> None:
    """Expected Behavior (Property 1) : les cibles de nav résolvent (aucun lien mort).

    Les fonctions Thèmes, Propositions, Statistiques, Équipes et Mandat, jadis
    absentes côté SSR (``404`` sur le code non corrigé), sont désormais montées
    par ``app/web/router.py`` (tâche 3.2) et rendent leur gabarit (tâche 3.3).
    Avec leurs Services d'appui disponibles (ici des doublures renvoyant des
    collections vides / des compteurs à zéro, comme dans
    ``tests/api/test_navigation_and_styling_preservation.py``), chaque route
    répond ``200`` : le menu d'en-tête ne comporte donc aucun lien mort.

    _Requirements: 2.1 (Expected Behavior / Property 1 from design.md)_
    """
    client = _client()
    candidate_paths = [
        "/themes",
        "/propositions",
        "/statistiques",
        "/equipes",
        "/mandat",
    ]
    statuses = {path: client.get(path).status_code for path in candidate_paths}

    dead_targets = [path for path, code in statuses.items() if code != 200]
    assert not dead_targets, (
        "Lien(s) mort(s) détecté(s) — ces cibles de navigation devraient résoudre "
        f"(200) sur le code corrigé. Statuts observés : {statuses!r}"
    )
