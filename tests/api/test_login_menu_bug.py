"""Test d'exploration de la condition de bug — point d'entrée de connexion absent.

Spec bugfix : ``.kiro/specs/user-login-menu-missing`` (tâche 1).

**Property 1: Bug Condition** — Point d'entrée de connexion absent et ``/login``
non résolvable.

**CRITIQUE** : ce test DOIT ÉCHOUER sur le code NON corrigé — l'échec confirme
que le bug existe. Il encode aussi le comportement attendu : il PASSERA une fois
le correctif appliqué (voir tâche 3.2), sans être réécrit.

On encode ``isBugCondition(X) where X.wants_login_entry_point = true`` des
*Correctness Properties* de design.md (Property 1) et des exigences
1.1, 1.2, 1.3, 2.1, 2.2, 2.3 de bugfix.md :

* **Cas 1** — pour tout chemin SSR public échantillonné, l'en-tête doit exposer
  un lien de menu (``<a href>``) vers ``/login`` de libellé « Connexion »
  (échoue sur le code non corrigé — ``NAV_ITEMS`` n'a que huit entrées).
* **Cas 2** — ``GET /login`` doit répondre ``200`` (échoue — actuellement
  ``404 NOT_FOUND``, aucune route SSR ``/login`` enregistrée).
* **Cas 3** — la réponse de ``/login`` doit être ``text/html`` et contenir un
  ``<form>`` ciblant ``/api/v1/auth/login`` avec un champ email
  (``type="email"``) et un champ mot de passe (``type="password"``) (échoue tant
  que ``login.html`` n'existe pas).

Portée : strictement la couche SSR/présentation. Aucun fichier ``app/api/v1/*``
ni Service n'est modifié — cette tâche n'écrit que le test d'exploration.

Réutilise les utilitaires observation-first de
``tests/api/test_navigation_and_styling_preservation.py`` : montage de
``web.web_router`` via ``TestClient`` avec la doublure ``_FakeProgramService``,
parsers HTML du ``<nav>`` (ici l'en-tête, premier ``<nav>``).

La partie « présence du lien » est formulée en test basé sur les propriétés
(Hypothesis) sur l'ensemble des chemins SSR publics échantillonnés.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.schemas.program import (
    ProgramMeasure,
    ProgramPageView,
    ProgramThemeGroup,
)
from app.schemas.proposal import Page
from app.schemas.statistics import GlobalStatistics
from app.web import router as web

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Répertoire des fichiers statiques réels, monté comme dans ``app/main.py``.
_STATIC_DIR = web.TEMPLATES_DIR.parent / "static"

# Cible attendue du point d'entrée de connexion (Exigences 1.3, 2.3).
_LOGIN_PATH = "/login"

# Libellé attendu de l'entrée de navigation « Connexion » (Exigence 2.1).
_LOGIN_LABEL = "Connexion"

# Endpoint backend existant que le formulaire doit cibler (Exigence 2.2).
_AUTH_LOGIN_ENDPOINT = "/api/v1/auth/login"

# Chemins RGPD publics (Exigence 3.2 — inclus dans l'échantillon SSR public).
_RGPD_PATHS = ["/mentions-legales", "/confidentialite", "/cookies", "/conditions"]

# Ensemble des chemins SSR publics échantillonnés pour la « présence du lien ».
_PUBLIC_SSR_PATHS = [
    "/programme",
    "/themes",
    "/propositions",
    "/statistiques",
    "/equipes",
    "/mandat",
    "/assistant",
    *_RGPD_PATHS,
]


# --------------------------------------------------------------------------- #
# Doublure de Service (sans base de données), alignée sur les conventions de   #
# tests/api/test_navigation_and_styling_preservation.py.                      #
# --------------------------------------------------------------------------- #
class _FakeProgramService:
    """``ProgramService`` factice renvoyant une :class:`ProgramPageView` fixe."""

    def __init__(self, view: ProgramPageView) -> None:
        self._view = view

    async def build_public_program_view(self) -> ProgramPageView:
        return self._view


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
# Doublures des autres Services SSR (sans base de données).                    #
#                                                                             #
# Les pages ``/themes``, ``/propositions``, ``/statistiques``, ``/equipes`` et #
# ``/mandat`` s'adossent à des Services liés à une session BD. Pour exercer la #
# seule couche de présentation (comme ``ProgramService``), on les remplace par  #
# des doublures renvoyant des collections vides : le router documente que ces  #
# pages sont rendues avec des listes vides plutôt qu'une erreur. Cela suffit à  #
# observer la présence du menu d'en-tête « Connexion » sur ces pages SSR.       #
# --------------------------------------------------------------------------- #
class _FakeThemeService:
    """``ThemeService`` factice : liste de Thèmes vide (rendu sans BD)."""

    async def list_themes(self) -> list[object]:
        return []


class _FakeProposalService:
    """``ProposalService`` factice : page de Propositions vide (rendu sans BD)."""

    async def list(self, *, page: int = 1, limit: int = 20) -> Page:
        return Page(items=[], total=0, page=page, limit=limit)


class _FakeStatisticsService:
    """``StatisticsService`` factice renvoyant des compteurs globaux fixes."""

    async def global_counts(self) -> GlobalStatistics:
        return GlobalStatistics(
            users=0, proposals=0, votes=0, comments=0, sources=0, themes=0
        )


class _FakeTeamService:
    """``TeamService`` factice : liste d'Équipes vide (rendu sans BD)."""

    async def list_teams(self) -> list[object]:
        return []


class _FakeMandateService:
    """``MandateService`` factice : Engagements et Indicateurs vides (rendu sans BD)."""

    async def list_commitments(self) -> list[object]:
        return []

    async def list_indicators(self) -> list[object]:
        return []


# --------------------------------------------------------------------------- #
# Application de test : routeur SSR + montage /static (aucune auth).           #
# --------------------------------------------------------------------------- #
def _build_ssr_app() -> FastAPI:
    """Assemble une application montant le routeur SSR et ``/static`` (aucune auth).

    Reproduit ``app/main.py`` pour les pages SSR : ``StaticFiles`` est monté sur
    ``/static`` et ``ProgramService`` est remplacé par une doublure afin
    d'exercer uniquement la couche de présentation, sans BD.
    """
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    app.include_router(web.web_router)
    app.dependency_overrides[web.get_program_service] = lambda: _FakeProgramService(
        _sample_program_view()
    )
    # Doublures des autres Services SSR pour exercer la présentation sans BD.
    app.dependency_overrides[web.get_theme_service] = lambda: _FakeThemeService()
    app.dependency_overrides[web.get_proposal_service] = lambda: _FakeProposalService()
    app.dependency_overrides[web.get_statistics_service] = (
        lambda: _FakeStatisticsService()
    )
    app.dependency_overrides[web.get_team_service] = lambda: _FakeTeamService()
    app.dependency_overrides[web.get_mandate_service] = lambda: _FakeMandateService()
    return app


def _ssr_client() -> TestClient:
    return TestClient(_build_ssr_app())


# --------------------------------------------------------------------------- #
# Parser HTML : liens du <nav> d'en-tête (premier <nav>).                      #
# --------------------------------------------------------------------------- #
class _HeaderNavParser(HTMLParser):
    """Extrait ``(texte, href)`` des ``<a>`` du **premier** ``<nav>`` (en-tête).

    ``base.html`` rend le ``<nav>`` d'en-tête d'abord, puis le ``<nav>`` du pied
    de page. On ne retient donc que le premier ``<nav>`` rencontré.
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

    def header_links(self) -> list[tuple[str, str]]:
        return self._navs[0] if self._navs else []


def _header_links(html: str) -> list[tuple[str, str]]:
    parser = _HeaderNavParser()
    parser.feed(html)
    return parser.header_links()


# --------------------------------------------------------------------------- #
# Cas 1 — Absence d'entrée « Connexion » (property sur les chemins SSR).       #
# --------------------------------------------------------------------------- #
@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=st.sampled_from(_PUBLIC_SSR_PATHS))
def test_header_nav_exposes_connexion_login_link(path: str) -> None:
    """Pour tout chemin SSR public, l'en-tête expose un lien « Connexion » → ``/login``.

    Property (Bug Condition) : sur le code NON corrigé, ce test ÉCHOUE — aucun
    ``<a>`` du ``<nav>`` d'en-tête ne pointe vers ``/login`` (``NAV_ITEMS`` n'a
    que huit entrées, sans « Connexion »). L'échec prouve que le bug existe.

    _Requirements: 1.1, 1.2, 2.1_
    """
    html = _ssr_client().get(path).text
    links = _header_links(html)

    login_links = [(label, href) for label, href in links if href == _LOGIN_PATH]
    assert login_links, (
        f"Chemin {path} : aucun lien de menu d'en-tête ne pointe vers "
        f"{_LOGIN_PATH!r}. Liens observés : {links!r}."
    )
    labels = {label for label, _href in login_links}
    assert _LOGIN_LABEL in labels, (
        f"Chemin {path} : lien vers {_LOGIN_PATH!r} trouvé mais sans libellé "
        f"« {_LOGIN_LABEL} » (libellés : {labels!r})."
    )


# --------------------------------------------------------------------------- #
# Cas 2 — Route /login manquante.                                             #
# --------------------------------------------------------------------------- #
def test_login_route_responds_200() -> None:
    """``GET /login`` doit répondre ``200`` (Exigences 1.3, 2.3).

    Sur le code NON corrigé, ce test ÉCHOUE — aucune route SSR ``/login`` n'est
    enregistrée, la réponse est ``404 NOT_FOUND``.

    _Requirements: 1.3, 2.3_
    """
    response = _ssr_client().get(_LOGIN_PATH)
    assert response.status_code == 200, (
        f"GET {_LOGIN_PATH} : statut {response.status_code} (attendu 200). "
        "Le code non corrigé renvoie 404 NOT_FOUND (aucune route SSR /login)."
    )


# --------------------------------------------------------------------------- #
# Cas 3 — Gabarit de connexion (form ciblant /api/v1/auth/login).             #
# --------------------------------------------------------------------------- #
def test_login_page_renders_form_targeting_auth_endpoint() -> None:
    """``/login`` rend un ``<form>`` email + mot de passe ciblant l'API ``auth``.

    Sur le code NON corrigé, ce test ÉCHOUE — la page n'existe pas (``404``) et
    aucun ``login.html`` ne contient de formulaire.

    _Requirements: 2.2, 2.3_
    """
    response = _ssr_client().get(_LOGIN_PATH)

    assert response.status_code == 200, (
        f"GET {_LOGIN_PATH} : statut {response.status_code} (attendu 200)."
    )
    assert response.headers["content-type"].startswith("text/html"), (
        f"GET {_LOGIN_PATH} : type {response.headers.get('content-type')!r} "
        "(attendu text/html)."
    )

    body = response.text
    assert "<form" in body.lower(), (
        f"La page {_LOGIN_PATH!r} doit contenir un <form> de connexion."
    )
    assert _AUTH_LOGIN_ENDPOINT in body, (
        f"Le formulaire de {_LOGIN_PATH!r} doit cibler {_AUTH_LOGIN_ENDPOINT!r} "
        "(non trouvé dans la réponse)."
    )
    assert re.search(r'type=["\']email["\']', body, re.IGNORECASE), (
        "Le formulaire de connexion doit exposer un champ email (type=\"email\")."
    )
    assert re.search(r'type=["\']password["\']', body, re.IGNORECASE), (
        "Le formulaire de connexion doit exposer un champ mot de passe "
        "(type=\"password\")."
    )
