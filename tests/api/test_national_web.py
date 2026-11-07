"""SSR pages of the national simulator (fragments, errors, accessibility markers)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import national_calc_rate_limit, scenario_save_rate_limit
from app.schemas.national_simulation import MobilisationGap
from app.services.national_simulation_service import (
    NationalSimulationService,
    ServiceUnavailableError,
)
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web import national as web_national
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api


class _Service(NationalSimulationService):
    async def mobilisation(self, scenario_rate: Decimal) -> MobilisationGap:
        return MobilisationGap(status="unavailable", scenario_rate=scenario_rate, message="n/d")


class _Broken(_Service):
    async def calculate(self, scenario):  # type: ignore[no-untyped-def]
        raise ServiceUnavailableError("down")


@pytest.fixture
def app_() -> FastAPI:
    app = FastAPI()
    app.include_router(web_national.national_web_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[national_calc_rate_limit] = lambda: None
    app.dependency_overrides[scenario_save_rate_limit] = lambda: None
    return app


@pytest.fixture
def client(app_: FastAPI, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(
        web_national, "_simulation_service", lambda s: _Service(BootstrapSnapshotProvider())
    )
    return TestClient(app_, raise_server_exceptions=False)


def test_simulator_page_has_labels_and_live_region(client: TestClient) -> None:
    html = client.get("/simulateur-national").text
    assert 'for="participation_rate"' in html
    assert 'for="red_restaurants"' in html
    assert 'aria-live="polite"' in html
    assert "http://" not in html.replace("http://www.w3.org", "")


def test_calculate_fragment_has_mentions_and_equivalent_table(client: TestClient) -> None:
    response = client.post(
        "/simulateur-national/calcul",
        data={"participation_rate": "10", "red_restaurants": "20", "horizon": "medium"},
    )
    assert response.status_code == 200
    html = response.text
    assert 'scope="col"' in html and 'scope="row"' in html
    assert "Version du modèle" in html and "Date de calcul" in html
    assert "Seuil minimal" in html and "Hypothèse" in html and "estimation" in html.lower()


def test_invalid_input_returns_422_fragment_with_retry(client: TestClient) -> None:
    response = client.post(
        "/simulateur-national/calcul", data={"participation_rate": "abc", "horizon": "medium"}
    )
    assert response.status_code == 422
    assert "Réessayer" in response.text


def test_unavailable_returns_503_fragment(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        web_national, "_simulation_service", lambda s: _Broken(BootstrapSnapshotProvider())
    )
    response = client.post(
        "/simulateur-national/calcul",
        data={"participation_rate": "10", "red_restaurants": "20", "horizon": "medium"},
    )
    assert response.status_code == 503
    assert "Réessayer" in response.text


def test_compare_needs_two_scenarios_then_renders(client: TestClient) -> None:
    one = {"s1_participation_rate": "10", "s1_red_restaurants": "20"}
    assert client.post("/simulateur-national/comparer/calcul", data=one).status_code == 422
    two = {**one, "s2_participation_rate": "20", "s2_red_clothing": "30"}
    response = client.post("/simulateur-national/comparer/calcul", data=two)
    assert response.status_code == 200
    assert "Scénario 2" in response.text and 'scope="row"' in response.text


def test_saved_page_anonymous_invites_login(client: TestClient) -> None:
    html = client.get("/mes-scenarios").text
    assert "/login" in html
