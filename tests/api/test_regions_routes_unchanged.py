"""Regression: the /regions routes and contextual links survive the header change.

Property 7; requirements 1.4, 1.5, 1.6, 6.2. No database needed: routes are checked
in the router registry and links on the DB-free simulator page and in templates.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import national_calc_rate_limit, scenario_save_rate_limit
from app.services.national_simulation_service import NationalSimulationService
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web import national as web_national
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

TEMPLATES = Path(__file__).resolve().parents[2] / "app" / "templates"


def test_regions_routes_still_registered() -> None:
    routes = {
        r.path: r
        for r in web_national.national_web_router.routes
        if getattr(r, "path", None) in {"/regions", "/regions/{code}"}
    }
    assert set(routes) == {"/regions", "/regions/{code}"}
    assert all("GET" in r.methods for r in routes.values())
    assert routes["/regions"].name == "regions"
    assert routes["/regions/{code}"].name == "region_detail"


def test_contextual_regions_links_remain_in_templates() -> None:
    expected = {
        "public/national_simulator.html": 'href="/regions"',
        "public/methodology.html": 'href="/regions"',
        "public/region_detail.html": 'href="/regions"',
        "public/regions.html": 'href="/regions/{{ r.code }}"',
    }
    for name, needle in expected.items():
        assert needle in (TEMPLATES / name).read_text(encoding="utf-8"), name


def test_rendered_simulator_page_links_to_regions(monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(web_national.national_web_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[national_calc_rate_limit] = lambda: None
    app.dependency_overrides[scenario_save_rate_limit] = lambda: None
    monkeypatch.setattr(
        web_national,
        "_simulation_service",
        lambda s: NationalSimulationService(BootstrapSnapshotProvider()),
    )
    response = TestClient(app, raise_server_exceptions=False).get("/simulateur-national")
    assert response.status_code == 200
    assert 'href="/regions"' in response.text
