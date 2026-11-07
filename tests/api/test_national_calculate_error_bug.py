"""Bug condition exploration: CALCULER returns 503 when the snapshot lacks the population.

**Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.2, 2.3**

Property 1 (Bug Condition): for a valid scenario and a model snapshot WITHOUT
``reference_population`` / ``adults``, the national simulator must compute with the
provisional bootstrap population (HTTP 200, population > 0, not "official",
non-null ``confidence_warning``). When the mobilisation gap cannot be computed the
scenario result is still returned with a mobilisation status "unavailable".

These tests encode the EXPECTED behaviour: they FAIL on unfixed code (the
``MissingPopulationError`` is masked as ``ServiceUnavailableError`` / HTTP 503).
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.rate_limit import national_calc_rate_limit, scenario_save_rate_limit
from app.economics.defaults import bootstrap_snapshot
from app.economics.national import (
    POPULATION_KEY,
    POPULATION_PARAMETER,
    NationalScenario,
    reference_population,
)
from app.economics.types import ModelSnapshot
from app.services import national_simulation_service as nss
from app.services.national_simulation_service import NationalSimulationService
from app.services.saved_scenario_service import SavedScenarioService
from app.web import national as web_national
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.api

_BOOTSTRAP = bootstrap_snapshot()
_PROVISIONAL_POPULATION = reference_population(_BOOTSTRAP).value
_CODES = list(_BOOTSTRAP.category_codes)


def _stripped() -> ModelSnapshot:
    """Bootstrap snapshot without the reference population (the bug condition)."""
    params = {
        k: v
        for k, v in _BOOTSTRAP.parameters.items()
        if not (k[0] == POPULATION_PARAMETER and k[2] == POPULATION_KEY)
    }
    snapshot = replace(_BOOTSTRAP, parameters=params)
    assert not snapshot.has(POPULATION_PARAMETER)
    return snapshot


class _Provider:
    model_version_id = None

    async def get_snapshot(self) -> ModelSnapshot:
        return _stripped()


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value

    def scalar_one(self) -> object:
        return self._value


class _FakeSession:
    """Minimal async session for SavedScenarioService.save."""

    def __init__(self) -> None:
        self.added: list[object] = []

    async def execute(self, statement: object) -> _FakeResult:
        text = str(statement)
        return _FakeResult(0 if "count" in text.lower() else 1)

    def add(self, row: object) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        return None

    async def refresh(self, row: object) -> None:
        return None


def _assert_provisional(result: nss.NationalScenarioResult) -> None:
    assert result.population.value > 0
    assert result.population.value == _PROVISIONAL_POPULATION
    assert result.population.confidence_label != "official"
    assert result.confidence_warning is not None


scenarios = st.builds(
    NationalScenario,
    participation_rate=st.decimals(
        min_value=Decimal("0"), max_value=Decimal("100"), places=1, allow_nan=False
    ),
    reductions=st.dictionaries(
        st.sampled_from(_CODES),
        st.decimals(min_value=Decimal("1"), max_value=Decimal("100"), places=1, allow_nan=False),
        min_size=1,
    ),
)


@pytest.mark.asyncio
@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(scenario=scenarios)
async def test_calculate_without_population_uses_provisional_fallback(
    scenario: NationalScenario,
) -> None:
    service = NationalSimulationService(_Provider())
    result = await service.calculate(scenario)
    _assert_provisional(result)


@pytest.mark.asyncio
async def test_compare_without_population_uses_provisional_fallback() -> None:
    service = NationalSimulationService(_Provider())
    a = NationalScenario(Decimal("10"), {_CODES[0]: Decimal("20")})
    b = NationalScenario(Decimal("20"), {_CODES[1]: Decimal("30")})
    comparison = await service.compare([a, b])
    assert len(comparison.results) == 2
    for result in comparison.results:
        _assert_provisional(result)


@pytest.mark.asyncio
async def test_save_without_population_succeeds() -> None:
    session = _FakeSession()
    service = SavedScenarioService(session, _Provider())  # type: ignore[arg-type]
    saved = await service.save(1, "mon scénario", NationalScenario(Decimal("10"), {_CODES[0]: Decimal("20")}))
    assert saved.model_version
    assert len(session.added) == 1


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI()
    app.include_router(web_national.national_web_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[national_calc_rate_limit] = lambda: None
    app.dependency_overrides[scenario_save_rate_limit] = lambda: None
    # Real service (real mobilisation, no DB session) on the population-less snapshot.
    monkeypatch.setattr(
        web_national, "_simulation_service", lambda s: NationalSimulationService(_Provider())
    )
    return TestClient(app, raise_server_exceptions=False)


def test_calculer_route_without_population_returns_result(client: TestClient) -> None:
    response = client.post(
        "/simulateur-national/calcul",
        data={"participation_rate": "10", "red_restaurants": "20", "horizon": "medium"},
    )
    assert response.status_code == 200, response.text[:300]
    assert "momentanément indisponible" not in response.text


def test_compare_route_without_population_returns_result(client: TestClient) -> None:
    data = {
        "s1_participation_rate": "10",
        "s1_red_restaurants": "20",
        "s2_participation_rate": "20",
        "s2_red_clothing": "30",
    }
    response = client.post("/simulateur-national/comparer/calcul", data=data)
    assert response.status_code == 200, response.text[:300]


# --------------------------------------------------------------------------- #
# 1.3 / 2.3: mobilisation degrades to "unavailable" instead of failing
# --------------------------------------------------------------------------- #
class _FullProvider:
    async def get_snapshot(self) -> ModelSnapshot:
        return _BOOTSTRAP


@pytest.mark.asyncio
async def test_mobilisation_unavailable_when_aggregate_lookup_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def boom(self: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(nss.AggregationService, "latest", boom)
    service = NationalSimulationService(_FullProvider(), _FakeSession())  # type: ignore[arg-type]
    gap = await service.mobilisation(Decimal("10"))
    assert gap.status == "unavailable"


@pytest.mark.asyncio
async def test_mobilisation_unavailable_without_aggregate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def none(self: object) -> None:
        return None

    monkeypatch.setattr(nss.AggregationService, "latest", none)
    service = NationalSimulationService(_FullProvider(), _FakeSession())  # type: ignore[arg-type]
    gap = await service.mobilisation(Decimal("10"))
    assert gap.status == "unavailable"


@pytest.mark.asyncio
async def test_mobilisation_unavailable_without_session() -> None:
    service = NationalSimulationService(_FullProvider(), None)
    gap = await service.mobilisation(Decimal("10"))
    assert gap.status == "unavailable"


def test_calculer_route_returns_result_when_mobilisation_fails(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def boom(self: object) -> None:
        raise RuntimeError("db down")

    monkeypatch.setattr(nss.AggregationService, "latest", boom)
    response = client.post(
        "/simulateur-national/calcul",
        data={"participation_rate": "10", "red_restaurants": "20", "horizon": "medium"},
    )
    assert response.status_code == 200, response.text[:300]
