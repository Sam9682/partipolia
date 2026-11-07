"""Dashboard API, commitment endpoint protection and the Partipolia pages."""

from __future__ import annotations

import json
import re

import pytest
from app.api.v1 import commitments, dashboard
from app.core.database import get_session
from app.core.middleware import register_exception_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api


class _EmptySession:
    """Session with no stored aggregate."""

    async def scalar(self, *_a, **_k):
        return None

    async def execute(self, *_a, **_k):
        class _R:
            def scalars(self):
                return iter(())

        return _R()


@pytest.fixture
def api() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(dashboard.router, prefix="/api/v1/dashboard")
    app.include_router(commitments.router, prefix="/api/v1/commitments")
    app.include_router(commitments.profile_router, prefix="/api/v1/citizens")
    app.dependency_overrides[get_session] = lambda: _EmptySession()
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def site() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


# ------------------------------------------------------------------ dashboard
def test_dashboard_without_data_is_suppressed_and_public(api: TestClient) -> None:
    response = api.get("/api/v1/dashboard/national")  # no authentication needed
    assert response.status_code == 200
    data = response.json()
    assert data["suppressed"] is True
    assert data["participants"] is None and data["fiscal"] is None
    assert data["personal_savings"] is None
    assert data["min_cohort"] >= 10  # a real anonymity threshold
    assert data["participants_label"] == "aucun participant"
    assert any("estimations" in d for d in data["disclaimers"])
    assert any("population" in d for d in data["disclaimers"])


def test_history_and_sectors_are_empty_without_data(api: TestClient) -> None:
    assert api.get("/api/v1/dashboard/history").json()["points"] == []
    sectors = api.get("/api/v1/dashboard/sectors").json()
    assert sectors["suppressed"] is True and sectors["sectors"] == []


# ------------------------------------------------------------------ protection
@pytest.mark.parametrize(
    "method,path",
    [
        ("post", "/api/v1/commitments"),
        ("get", "/api/v1/commitments/me"),
        ("put", "/api/v1/commitments/1"),
        ("delete", "/api/v1/commitments/1"),
        ("get", "/api/v1/citizens/me"),
        ("put", "/api/v1/citizens/me"),
    ],
)
def test_commitment_and_profile_routes_require_authentication(
    api: TestClient, method: str, path: str
) -> None:
    response = getattr(api, method)(path, **({"json": {}} if method in {"post", "put"} else {}))
    assert response.status_code == 401


def test_commitment_payload_validation() -> None:
    from decimal import Decimal

    from app.schemas.commitment import CommitmentCreate, CommitmentUpdate

    with pytest.raises(ValueError):
        CommitmentCreate(category_code="x", reduction_percentage=Decimal("10"), duration_months=2)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        CommitmentUpdate(reduction_percentage=Decimal("-1"))
    with pytest.raises(ValueError):
        CommitmentUpdate(duration_months=3, end_date="2030-01-01")  # type: ignore[arg-type]


# ------------------------------------------------------------------ pages
def test_national_page(site: TestClient) -> None:
    html = site.get("/impact-national").text
    assert 'data-testid="dashboard"' in html and 'data-testid="dashboard-disclaimers"' in html
    assert "chart.umd.min.js" in html
    embedded = re.search(r'id="sector-labels">(.*?)</script>', html, re.S)
    assert embedded is not None
    labels = json.loads(embedded.group(1))
    assert labels["IZ"] == "Hébergement et restauration"  # French sector labels are embedded
    assert len(labels) == 37


def test_join_page_has_explicit_consent_and_declared_notice(site: TestClient) -> None:
    html = site.get("/participer").text
    assert 'data-testid="join"' in html
    assert "anonyme" in html and "agrégée" in html  # explicit aggregation consent
    assert "déclaratifs" in html
    assert len(re.findall(r'type="checkbox"', html)) >= 1


def test_methodology_lists_sources_and_provisional_parameters(site: TestClient) -> None:
    html = site.get("/methodologie").text
    assert "Estimations, pas mesures" in html
    assert "insee.fr" in html and "service-public.gouv.fr" in html
    assert "fuel_excise_per_l" in html  # an unverified parameter must be disclosed
    assert "employment_elasticity" in html
    assert "Version du modèle" in html


def test_simulator_page_shows_fiscal_block(site: TestClient) -> None:
    html = site.get("/simulateur").text
    assert 'data-testid="fiscal"' in html and "provisoire" in html
