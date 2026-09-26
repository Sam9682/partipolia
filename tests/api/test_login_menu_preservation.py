"""Tests de préservation — Comportement inchangé hors condition de bug.

Spec bugfix : ``.kiro/specs/user-login-menu-missing`` (tâche 2).

**Property 2: Preservation** — Comportement inchangé hors condition de bug.

Méthodologie **observation-first** : on relève d'abord la ligne de base sur le
code NON corrigé (les huit ``NAV_ITEMS`` et leur ordre, l'accès public sans
authentification aux pages SSR existantes, la forme/le statut d'un échantillon
d'endpoints ``/api/v1/*`` dont ``auth``, le repli ``base.html``), puis on
**affirme** cette ligne de base. Sur le code NON corrigé ces tests PASSENT : ils
capturent la ligne de base que le correctif (tâche 3) devra préserver.

On encode ``NOT isBugCondition(X)`` des *Preservation Requirements* de design.md
(Property 2) et des exigences 3.1, 3.2, 3.3, 3.4 de bugfix.md :

* **Ordre préservé** : pour tout chemin SSR public échantillonné, le ``<nav>``
  d'en-tête expose les huit entrées existantes **dans le même ordre**
  (labels + href) que la ligne de base observée (Exigence 3.1).
* **Accès public** : pour tout chemin SSR public échantillonné (``/programme``,
  ``/themes``, ``/propositions``, ``/statistiques``, ``/equipes``, ``/mandat``,
  ``/assistant``, pages RGPD), la réponse est ``200`` en ``text/html`` sans
  authentification (Exigence 3.2).
* **Endpoints ``auth`` / ``/api/v1/*`` préservés** : pour un échantillon
  d'endpoints, statut et forme de réponse restent identiques à la ligne de base
  (ex. ``POST /api/v1/auth/login`` avec identifiants invalides ⇒ ``401``
  générique) (Exigence 3.3).
* **Repli ``nav_items``** : le rendu de ``base.html`` sans ``nav_items`` affiche
  toujours au moins le lien « Programme » (Exigence 3.4).

Portée : strictement la couche SSR/présentation et l'observation de la surface
API existante. Aucun fichier ``app/api/v1/*`` ni source applicative n'est
modifié — cette tâche n'écrit que des tests.

Réutilise les utilitaires observation-first de
``tests/api/test_navigation_and_styling_preservation.py`` (montage de
``web.web_router`` via ``TestClient`` avec la doublure ``_FakeProgramService`` et
montage ``/static``, parsers HTML du ``<nav>``) et le motif de montage du routeur
``auth`` de ``tests/api/test_auth_api.py`` (doublure ``_FakeAuthService``, gardes
de débit neutralisées). Les propriétés sont formulées en test basé sur les
propriétés (Hypothesis) là où c'est pertinent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.v1 import auth as auth_api
from app.core.rate_limit import default_rate_limit
from app.schemas.auth import AccessToken, TokenPair
from app.schemas.program import (
    ProgramMeasure,
    ProgramPageView,
    ProgramThemeGroup,
)
from app.schemas.proposal import Page
from app.schemas.statistics import GlobalStatistics
from app.services.auth_service import GENERIC_CREDENTIALS_ERROR, AuthError
from app.web import router as web

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

# Répertoire des fichiers statiques réels, monté comme dans ``app/main.py``.
_STATIC_DIR = web.TEMPLATES_DIR.parent / "static"

# --------------------------------------------------------------------------- #
# LIGNE DE BASE OBSERVÉE (observation-first) sur le code NON corrigé.          #
# --------------------------------------------------------------------------- #
# Les huit entrées de navigation existantes, labels + href, DANS L'ORDRE
# (observé dans ``app.web.router.NAV_ITEMS``). Le correctif (tâche 3) doit
# préserver ces huit entrées et leur ordre — la neuvième « Connexion » venant
# APRÈS, sans altérer les huit premières (Exigence 3.1).
_BASELINE_NAV_ITEMS: list[tuple[str, str]] = [
    ("Programme", "/programme"),
    ("Thèmes", "/themes"),
    ("Propositions", "/propositions"),
    ("Statistiques", "/statistiques"),
    ("Équipes", "/equipes"),
    ("Mandat", "/mandat"),
    ("Assistant", "/assistant"),
    ("API", "/docs"),
]

# Chemins RGPD publics (Exigence 3.2 — inclus dans l'échantillon SSR public).
_RGPD_PATHS = ["/mentions-legales", "/confidentialite", "/cookies", "/conditions"]

# Ensemble des chemins SSR publics échantillonnés (Exigences 3.1, 3.2).
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

# Identifiants connus de la doublure d'auth (pour l'échantillon ``auth``).
_KNOWN_EMAIL = "citoyen@example.org"
_KNOWN_PASSWORD = "secret123"


# --------------------------------------------------------------------------- #
# Doublure de ProgramService (sans base de données), alignée sur              #
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
# observer la ligne de base « ``200`` en HTML sans auth » et le menu d'en-tête. #
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
# Doublure d'AuthService (sans base de données), alignée sur                   #
# tests/api/test_auth_api.py — messages génériques (Exigences 1.9, 3.3).      #
# --------------------------------------------------------------------------- #
class _FakeAuthService:
    """``AuthService`` factice reproduisant le contrat de connexion.

    Message d'erreur **générique** identique que l'email soit inconnu ou le mot
    de passe erroné (Exigence 1.9), reflétant le Service réel.
    """

    def __init__(self, *, known_emails: dict[str, str] | None = None) -> None:
        self._known = {e.lower(): p for e, p in (known_emails or {}).items()}

    async def authenticate(self, email: str, password: str) -> TokenPair:
        normalized = email.strip().lower()
        expected = self._known.get(normalized)
        if expected is None or expected != password:
            raise AuthError(GENERIC_CREDENTIALS_ERROR)
        return TokenPair(
            access_token="access-token-value",
            refresh_token="refresh-token-value",
            expires_in=900,
        )

    async def refresh(self, refresh_token: str) -> AccessToken:  # pragma: no cover
        raise AuthError(GENERIC_CREDENTIALS_ERROR)

    async def logout(self, session_id: str) -> None:  # pragma: no cover
        return None


# --------------------------------------------------------------------------- #
# Applications de test : routeur SSR + /static ; routeur auth (aucune BD).     #
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


def _build_auth_app(service: _FakeAuthService) -> FastAPI:
    """Monte le routeur ``auth`` sous ``/api/v1/auth`` (comme en production).

    Le service factice est injecté via la surcharge de ``get_auth_service`` ; les
    gardes de débit déclarées sur ``/login`` sont neutralisées pour éviter tout
    accès à Redis. Aucun fichier ``app/api/v1/*`` n'est modifié.
    """
    app = FastAPI()
    app.include_router(auth_api.router, prefix="/api/v1/auth", tags=["auth"])
    app.dependency_overrides[auth_api.get_auth_service] = lambda: service
    app.dependency_overrides[default_rate_limit("login")] = lambda: None
    app.dependency_overrides[default_rate_limit("register")] = lambda: None
    return app


def _auth_client() -> TestClient:
    return TestClient(
        _build_auth_app(_FakeAuthService(known_emails={_KNOWN_EMAIL: _KNOWN_PASSWORD}))
    )


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
# Exigence 3.1 — les huit entrées existantes et leur ordre sont préservés.     #
# --------------------------------------------------------------------------- #
def test_header_nav_baseline_eight_items_in_order() -> None:
    """Ligne de base explicite : le ``<nav>`` d'en-tête liste les huit entrées ordonnées (Exigence 3.1).

    Observé sur le code NON corrigé : les huit ``NAV_ITEMS`` (Programme … API)
    apparaissent dans cet ordre exact. Le correctif doit préserver ces huit
    entrées et leur ordre (la neuvième « Connexion » venant après).

    _Requirements: 3.1_
    """
    html = _ssr_client().get("/programme").text
    links = _header_links(html)

    # Les huit premières entrées observées doivent correspondre à la ligne de base.
    assert links[: len(_BASELINE_NAV_ITEMS)] == _BASELINE_NAV_ITEMS, (
        "Le <nav> d'en-tête doit préserver les huit entrées existantes dans "
        f"l'ordre {_BASELINE_NAV_ITEMS!r} ; observé : {links!r}."
    )


@settings(max_examples=25, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=st.sampled_from(_PUBLIC_SSR_PATHS))
def test_header_nav_order_preserved_on_all_ssr_paths(path: str) -> None:
    """Property (ordre préservé) : sur tout chemin SSR public, les huit entrées gardent leur ordre (Exigence 3.1).

    Le menu étant piloté par la source de vérité unique ``NAV_ITEMS`` via
    ``ssr_context``, chaque page SSR expose la même séquence. On vérifie que les
    huit entrées de la ligne de base apparaissent en tête, dans le même ordre,
    quelle que soit la page — indépendamment d'un éventuel ajout ultérieur.

    _Requirements: 3.1_
    """
    html = _ssr_client().get(path).text
    links = _header_links(html)

    assert links[: len(_BASELINE_NAV_ITEMS)] == _BASELINE_NAV_ITEMS, (
        f"Chemin {path} : les huit entrées d'en-tête doivent rester "
        f"{_BASELINE_NAV_ITEMS!r} dans l'ordre ; observé : {links!r}."
    )


# --------------------------------------------------------------------------- #
# Exigence 3.2 — accès public sans authentification à toutes les pages SSR.    #
# --------------------------------------------------------------------------- #
@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(path=st.sampled_from(_PUBLIC_SSR_PATHS))
def test_public_ssr_pages_render_200_html_without_auth(path: str) -> None:
    """Property (accès public) : tout chemin SSR public répond ``200`` en HTML sans auth (Exigence 3.2).

    Ligne de base observée sur le code NON corrigé : aucune de ces pages n'exige
    d'authentification ; toutes répondent ``200`` en ``text/html``. Le correctif
    ne doit pas restreindre cet accès public.

    _Requirements: 3.2_
    """
    response = _ssr_client().get(path)

    assert response.status_code == 200, (
        f"Chemin {path} : statut {response.status_code} (attendu 200, accès public)."
    )
    assert response.headers["content-type"].startswith("text/html"), (
        f"Chemin {path} : type {response.headers.get('content-type')!r} "
        "(attendu text/html)."
    )


# --------------------------------------------------------------------------- #
# Exigence 3.3 — endpoints /api/v1/auth/* préservés (statut + forme).          #
# --------------------------------------------------------------------------- #
def test_auth_login_invalid_credentials_returns_401_generic() -> None:
    """``POST /api/v1/auth/login`` avec identifiants invalides ⇒ ``401`` générique (Exigence 3.3).

    Ligne de base observée : un mot de passe erroné produit un ``401`` avec le
    message **générique** ``GENERIC_CREDENTIALS_ERROR``. Le correctif ne touche
    pas ``app/api/v1/*`` ; ce comportement doit rester identique.

    _Requirements: 3.3_
    """
    response = _auth_client().post(
        "/api/v1/auth/login",
        json={"email": _KNOWN_EMAIL, "password": "mauvais-mdp"},
    )

    assert response.status_code == 401, (
        f"POST /api/v1/auth/login (identifiants invalides) : statut "
        f"{response.status_code} (attendu 401)."
    )
    assert response.json()["detail"] == GENERIC_CREDENTIALS_ERROR, (
        "Le message d'erreur de connexion doit rester générique "
        f"({GENERIC_CREDENTIALS_ERROR!r})."
    )


def test_auth_login_unknown_email_same_generic_401() -> None:
    """Un email inconnu produit le **même** ``401`` générique qu'un mot de passe erroné (Exigence 3.3).

    Ligne de base observée : aucune distinction entre email inconnu et mot de
    passe erroné (Exigence 1.9). À préserver après correction.

    _Requirements: 3.3_
    """
    client = _auth_client()
    unknown = client.post(
        "/api/v1/auth/login",
        json={"email": "inconnu@example.org", "password": "peu-importe"},
    )
    wrong_password = client.post(
        "/api/v1/auth/login",
        json={"email": _KNOWN_EMAIL, "password": "mauvais-mdp"},
    )

    assert unknown.status_code == wrong_password.status_code == 401
    assert unknown.json()["detail"] == wrong_password.json()["detail"]
    assert unknown.json()["detail"] == GENERIC_CREDENTIALS_ERROR


def test_auth_login_valid_credentials_returns_200_token_pair() -> None:
    """Des identifiants valides renvoient ``200`` + couple access/refresh (Exigence 3.3).

    Ligne de base observée : forme de réponse ``TokenPair`` (access_token,
    refresh_token, token_type=bearer, expires_in) inchangée. À préserver.

    _Requirements: 3.3_
    """
    response = _auth_client().post(
        "/api/v1/auth/login",
        json={"email": _KNOWN_EMAIL, "password": _KNOWN_PASSWORD},
    )

    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "access_token",
        "refresh_token",
        "token_type",
        "expires_in",
    }
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["expires_in"] > 0


# --------------------------------------------------------------------------- #
# Exigence 3.4 — repli sûr de base.html sans ``nav_items``.                    #
# --------------------------------------------------------------------------- #
def test_base_html_fallback_shows_programme_without_nav_items() -> None:
    """Le rendu de ``base.html`` sans ``nav_items`` affiche au moins « Programme » (Exigence 3.4).

    Ligne de base observée : quand aucun ``nav_items`` n'est fourni au gabarit,
    ``base.html`` applique son repli sûr et rend au moins un lien « Programme »
    (``href="/programme"``). Ce repli doit rester intact après correction.

    _Requirements: 3.4_
    """
    # ``base.html`` n'utilise ni ``request`` ni ``url_for`` : on peut le rendre
    # directement via le moteur Jinja2 partagé, sans contexte de requête.
    html = web.templates.get_template("base.html").render()
    links = _header_links(html)

    programme_links = [
        (label, href) for label, href in links if href == "/programme"
    ]
    assert programme_links, (
        "Le repli de base.html (sans nav_items) doit rendre au moins un lien "
        f"vers /programme ; liens d'en-tête observés : {links!r}."
    )
    labels = {label for label, _href in programme_links}
    assert "Programme" in labels, (
        f"Le lien de repli doit porter le libellé « Programme » (libellés : {labels!r})."
    )
