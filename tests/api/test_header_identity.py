"""Header identity indicator: anonymous / signed-in display, robustness, placement."""

from __future__ import annotations

import logging
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

EMAIL = "alice.martin@example.org"

# Pages that render without PostgreSQL when no cookie is sent.
CONTENT_PATH = "/comprendre"
# Anonymous 422 page rendered through ``public/claim_message.html`` (no DB access).
MESSAGE_PATH = "/revendications?page=abc"

INDICATOR_RE = re.compile(
    r'<div[^>]*data-testid="identity-indicator">(.*?)</div>\s*<button', re.S
)


@pytest.fixture(scope="module")
def client() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


def _indicator(html: str) -> str:
    match = INDICATOR_RE.search(html)
    assert match is not None, "identity indicator not found"
    return match.group(1)


def _patch_user(monkeypatch: pytest.MonkeyPatch, email: str | None) -> None:
    """Make the header resolver return a user (the resolver calls the dependency directly)."""

    async def fake(request, session):  # noqa: ANN001, ANN202
        return SimpleNamespace(email=email) if email else None

    monkeypatch.setattr("app.web.public.get_current_user_optional", fake)


def test_no_cookie_shows_anonymous_and_login_link(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    indicator = _indicator(response.text)
    assert "Anonyme" in indicator
    assert re.search(r'<a href="/login"[^>]*>Connexion</a>', indicator)
    assert "@" not in indicator


def test_valid_user_shows_email_only(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_user(monkeypatch, EMAIL)
    response = client.get("/")
    assert response.status_code == 200
    indicator = _indicator(response.text)
    assert EMAIL in indicator
    assert "Anonyme" not in indicator
    assert "Connexion" not in indicator
    assert "logout" not in indicator.lower()
    assert "déconnexion" not in indicator.lower()


def test_invalid_cookie_shows_anonymous_with_2xx(client: TestClient) -> None:
    response = client.get("/", cookies={"access_token": "not.a.valid-token"})
    assert 200 <= response.status_code < 300
    indicator = _indicator(response.text)
    assert "Anonyme" in indicator
    assert "Connexion" in indicator


def test_resolver_failure_degrades_to_anonymous_and_logs(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def boom(request, session):  # noqa: ANN001, ANN202
        raise RuntimeError("db down")

    monkeypatch.setattr("app.web.public.get_current_user_optional", boom)
    with caplog.at_level(logging.ERROR, logger="app.web.public"):
        response = client.get("/")
    assert 200 <= response.status_code < 300
    assert "Anonyme" in _indicator(response.text)
    assert any(
        r.levelno >= logging.ERROR and "header identity resolution failed" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize("path", ["/", "/login", CONTENT_PATH, MESSAGE_PATH])
def test_indicator_present_on_pages(client: TestClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (200, 422)
    assert response.text.count('data-testid="identity-indicator"') == 1
    assert "Anonyme" in _indicator(response.text)


def test_indicator_present_on_regions_page(client: TestClient) -> None:
    response = client.get("/regions")
    if response.status_code != 200:
        pytest.skip("/regions requires PostgreSQL")
    assert response.text.count('data-testid="identity-indicator"') == 1


def test_indicator_outside_mobile_panel(client: TestClient) -> None:
    html = client.get("/").text
    panel = re.search(r'<nav x-show="open".*?</nav>', html, re.S)
    assert panel is not None
    assert "identity-indicator" not in panel.group(0)
    assert "Connexion" not in panel.group(0)
    header = re.search(r"<header.*?</header>", html, re.S)
    assert header is not None and "identity-indicator" in header.group(0)


def test_identity_not_fetched_client_side(client: TestClient) -> None:
    html = client.get("/").text
    assert "hx-get" not in html
    assert "fetch(" not in html
