"""Partipolia public shell: navigation, landing page, legacy routes hidden."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

EXPECTED_NAV = [
    ("Accueil", "/"),
    ("Comprendre", "/comprendre"),
    ("Simulateur", "/simulateur"),
    ("Simulateur national", "/simulateur-national"),
    ("Impact national", "/impact-national"),
    ("Régions", "/regions"),
    ("Nos revendications", "/nos-revendications"),
    ("Revendications citoyennes", "/revendications"),
    ("Consultations", "/consultations"),
    ("Méthodologie", "/methodologie"),
    ("Rejoindre", "/rejoindre"),
]

# Pages adossées à la base de données : exclues de l'échantillon sans PostgreSQL
# (leurs liens de menu restent vérifiés par ``test_navigation_items_and_order``).
DB_BACKED_PATHS = {"/regions", "/revendications", "/consultations"}

PUBLIC_PATHS = [href for _, href in EXPECTED_NAV if href not in DB_BACKED_PATHS] + [
    "/login",
    "/mentions-legales",
    "/confidentialite",
    "/cookies",
    "/conditions",
]

LEGACY_PATHS = [
    "/programme",
    "/themes",
    "/propositions",
    "/statistiques",
    "/equipes",
    "/mandat",
    "/assistant",
    "/reparer-la-loi",
    "/api/v1/proposals",
    "/api/v1/themes",
    "/api/v1/program",
    "/api/v1/statistics",
    "/api/v1/legal-problems",
]


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("path", PUBLIC_PATHS)
def test_public_pages_render(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code == 200
    assert 'lang="fr"' in response.text


def test_navigation_items_and_order(client: TestClient) -> None:
    html = client.get("/").text
    nav = re.search(r'data-testid="main-nav">(.*?)</nav>', html, re.S)
    assert nav is not None
    links = re.findall(r'<a href="([^"]+)"[^>]*>([^<]+)</a>', nav.group(1))
    assert [(label.strip(), href) for href, label in links] == EXPECTED_NAV


@pytest.mark.parametrize("path", LEGACY_PATHS)
def test_legacy_routes_are_hidden(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_pages_do_not_load_external_assets(client: TestClient) -> None:
    for path in PUBLIC_PATHS:
        html = client.get(path).text
        # No script, stylesheet, image or iframe may be loaded from another origin
        # (plain citation links to official sources are allowed).
        assert not re.search(r'<(script|link|img|iframe)\b[^>]*\b(src|href)="https?://', html), path
        assert "cdn." not in html and "unpkg" not in html, path


def test_static_assets_are_served(client: TestClient) -> None:
    for asset in (
        "css/partipolia.css",
        "js/htmx.min.js",
        "js/alpine.min.js",
        "js/chart.umd.min.js",
    ):
        assert client.get(f"/static/{asset}").status_code == 200, asset


def test_landing_page_content(client: TestClient) -> None:
    html = client.get("/").text
    for text in (
        "PARTI<span",
        "La reprise en main par les citoyens.",
        "Après l'échec des élites politiques.",
        "Modifiez la trajectoire ou vous n'aurez plus un rond :)",
        "JE REPRENDS LE CONTRÔLE",
        "CALCULER MON IMPACT",
        "Votre argent. Votre pouvoir. Votre décision.",
    ):
        assert text in html, text
    assert 'href="/rejoindre"' in html and 'href="/simulateur"' in html


def test_landing_page_sections_and_categories(client: TestClient) -> None:
    html = client.get("/").text
    for section_id in (
        "pourquoi",
        "comment",
        "categories",
        "impact-personnel",
        "impact-national",
        "recettes",
        "revendications",
        "transparence",
        "rejoindre",
    ):
        assert f'id="{section_id}"' in html, section_id
    assert html.count("data-category=") == 6
    assert "4\u00a0170\u00a0€" in html


def test_global_disclaimer_present(client: TestClient) -> None:
    html = client.get("/").text
    assert "estimations modélisées" in html
    assert "déclaratifs" in html
