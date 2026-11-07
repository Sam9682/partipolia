"""Preservation: national simulator behaviour OUTSIDE the bug condition (Property 2).

Spec ``national-simulator-calculate-error``, task 2. These tests are written
observation-first on the UNFIXED code and MUST pass both before and after the fix.
Bug condition = valid scenario AND snapshot without ``reference_population`` /
``adults``; every test below uses inputs where that condition is FALSE.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

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
from app.economics.types import Confidence, ModelSnapshot, ParameterRange
from app.services import national_simulation_service as svc_module
from app.services.national_simulation_service import (
    NationalSimulationService,
    ServiceUnavailableError,
)
from app.web import national as web_national
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

pytestmark = pytest.mark.api

D = Decimal
ERROR_503 = "Le simulateur national est momentanément indisponible."
FORM = {"participation_rate": "10", "red_restaurants": "20", "horizon": "medium"}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
class _Provider:
    def __init__(self, snapshot: ModelSnapshot) -> None:
        self._snapshot = snapshot

    async def get_snapshot(self) -> ModelSnapshot:
        return self._snapshot


class _FailingProvider:
    async def get_snapshot(self) -> ModelSnapshot:
        raise RuntimeError("database down")


def _with_population(value: Decimal, confidence: Confidence, note: str) -> ModelSnapshot:
    base = bootstrap_snapshot()
    params = dict(base.parameters)
    params[(POPULATION_PARAMETER, None, POPULATION_KEY)] = ParameterRange(
        low=value, central=value, high=value, confidence=confidence, unit="persons", note=note
    )
    return replace(base, parameters=params)


def _without_other_parameters() -> ModelSnapshot:
    """Population kept; the VAT profiles removed (genuine failure of the direct module)."""
    return replace(bootstrap_snapshot(), vat_profiles={})


class _NoMobilisation(NationalSimulationService):
    async def mobilisation(self, scenario_rate):  # type: ignore[no-untyped-def]
        from app.schemas.national_simulation import MobilisationGap

        return MobilisationGap(status="unavailable", scenario_rate=scenario_rate, message="n/d")


def _run(coro):  # type: ignore[no-untyped-def]
    return asyncio.run(coro)


def _client(monkeypatch: pytest.MonkeyPatch, service: NationalSimulationService) -> TestClient:
    app = FastAPI()
    app.include_router(web_national.national_web_router)
    app.dependency_overrides[get_current_user_optional] = lambda: None
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[national_calc_rate_limit] = lambda: None
    app.dependency_overrides[scenario_save_rate_limit] = lambda: None
    monkeypatch.setattr(web_national, "_simulation_service", lambda s: service)
    return TestClient(app, raise_server_exceptions=False)


# --------------------------------------------------------------------------- #
# 3.1 Population of the snapshot is used as is (Property 2)
# --------------------------------------------------------------------------- #
_scenarios = st.builds(
    NationalScenario,
    participation_rate=st.decimals(min_value=0, max_value=100, places=2),
    reductions=st.dictionaries(
        st.sampled_from([c.code for c in bootstrap_snapshot().categories]),
        st.decimals(min_value=0, max_value=100, places=2),
        max_size=4,
    ),
    horizon=st.sampled_from(["short", "medium", "long"]),
)
_populations = st.tuples(
    st.integers(min_value=1_000_000, max_value=90_000_000),
    st.sampled_from([Confidence.HIGH, Confidence.MEDIUM, Confidence.PROVISIONAL, Confidence.LOW]),
)


@settings(max_examples=40, deadline=None, suppress_health_check=list(HealthCheck))
@given(scenario=_scenarios, pop=_populations)
def test_population_value_source_and_label_preserved(
    scenario: NationalScenario, pop: tuple[int, Confidence]
) -> None:
    """**Validates: Requirements 3.1**"""
    value, confidence = pop
    note = "INSEE test population 2024-01-01"
    snapshot = _with_population(D(value), confidence, note)
    expected = reference_population(snapshot)

    result = _run(NationalSimulationService(_Provider(snapshot)).calculate(scenario))

    assert result.population.value == D(value)
    assert result.population.source == note
    assert result.population.confidence_label == expected.confidence_label
    assert result.population.as_of == expected.as_of
    assert result.model_version == snapshot.model_version
    # participants derive from the snapshot's own population
    assert result.participants == (D(value) * scenario.participation_rate / 100).quantize(
        D(1), rounding="ROUND_HALF_EVEN"
    )
    # An official population never gets a weak-population label substituted.
    if confidence is Confidence.HIGH:
        assert result.population.confidence_label == "official"


def test_bootstrap_snapshot_population_preserved_through_compare() -> None:
    """**Validates: Requirements 3.1**"""
    snapshot = bootstrap_snapshot()
    expected = reference_population(snapshot)
    scenarios = [
        NationalScenario(D("10"), {"restaurants": D("20")}),
        NationalScenario(D("25"), {"clothing": D("30")}),
    ]
    comparison = _run(NationalSimulationService(_Provider(snapshot)).compare(scenarios))
    assert len(comparison.results) == 2
    for result in comparison.results:
        assert result.population.value == expected.value
        assert result.population.source == expected.source
        assert result.population.confidence_label == expected.confidence_label


# --------------------------------------------------------------------------- #
# 3.2 Validation errors stay 422
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({"horizon": "medium"}, "obligatoire"),
        ({"participation_rate": "abc", "horizon": "medium"}, "saisissez un nombre"),
        ({"participation_rate": "150", "horizon": "medium"}, "Paramètre invalide"),
        ({"participation_rate": "-5", "horizon": "medium"}, "Paramètre invalide"),
        ({"participation_rate": "10", "red_restaurants": "x"}, "saisissez un nombre"),
        ({"participation_rate": "10", "red_restaurants": "120"}, "Paramètre invalide"),
        ({"participation_rate": "10", "red_clothing": "-1"}, "Paramètre invalide"),
    ],
)
def test_invalid_form_returns_422_with_message(
    monkeypatch: pytest.MonkeyPatch, data: dict[str, str], message: str
) -> None:
    """**Validates: Requirements 3.2**"""
    service = _NoMobilisation(_Provider(bootstrap_snapshot()))
    response = _client(monkeypatch, service).post("/simulateur-national/calcul", data=data)
    assert response.status_code == 422
    assert message in response.text
    assert "Réessayer" in response.text
    assert ERROR_503 not in response.text


@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
@given(raw=st.text(alphabet="abcxyz!?#", min_size=1, max_size=6))
def test_non_numeric_participation_always_422(raw: str) -> None:
    """**Validates: Requirements 3.2**"""
    mp = pytest.MonkeyPatch()
    try:
        service = _NoMobilisation(_Provider(bootstrap_snapshot()))
        response = _client(mp, service).post(
            "/simulateur-national/calcul", data={"participation_rate": raw}
        )
    finally:
        mp.undo()
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# 3.3 Genuine engine failures stay 503 with no partial result
# --------------------------------------------------------------------------- #
def test_unknown_category_raises_service_unavailable() -> None:
    """**Validates: Requirements 3.3**"""
    service = NationalSimulationService(_Provider(bootstrap_snapshot()))
    with pytest.raises(ServiceUnavailableError):
        _run(service.calculate(NationalScenario(D("10"), {"does_not_exist": D("20")})))


def test_unknown_category_in_compare_yields_no_partial_comparison() -> None:
    """**Validates: Requirements 3.3**"""
    service = NationalSimulationService(_Provider(bootstrap_snapshot()))
    scenarios = [
        NationalScenario(D("10"), {"restaurants": D("20")}),
        NationalScenario(D("10"), {"does_not_exist": D("20")}),
    ]
    with pytest.raises(ServiceUnavailableError):
        _run(service.compare(scenarios))


def test_missing_economic_parameter_other_than_population_is_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Validates: Requirements 3.3**"""
    service = _NoMobilisation(_Provider(_without_other_parameters()))
    with pytest.raises(ServiceUnavailableError):
        _run(service.calculate(NationalScenario(D("10"), {"restaurants": D("20")})))
    response = _client(monkeypatch, service).post("/simulateur-national/calcul", data=FORM)
    assert response.status_code == 503
    assert ERROR_503 in response.text
    assert "Réessayer" in response.text
    assert "Version du modèle" not in response.text  # no partial result


def test_snapshot_provider_failure_is_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.3**"""
    service = _NoMobilisation(_FailingProvider())  # type: ignore[arg-type]
    with pytest.raises(ServiceUnavailableError):
        _run(service.calculate(NationalScenario(D("10"), {"restaurants": D("20")})))
    client = _client(monkeypatch, service)
    response = client.post("/simulateur-national/calcul", data=FORM)
    assert response.status_code == 503
    assert ERROR_503 in response.text
    assert "Version du modèle" not in response.text
    compare = client.post(
        "/simulateur-national/comparer/calcul",
        data={
            "s1_participation_rate": "10",
            "s1_red_restaurants": "20",
            "s2_participation_rate": "20",
            "s2_red_clothing": "30",
        },
    )
    assert compare.status_code == 503
    assert ERROR_503 in compare.text


# --------------------------------------------------------------------------- #
# 3.4 Rate limit stays 429
# --------------------------------------------------------------------------- #
def test_rate_limit_exceeded_returns_429(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.4**"""
    service = _NoMobilisation(_Provider(bootstrap_snapshot()))
    client = _client(monkeypatch, service)

    def _limited() -> None:
        raise HTTPException(status_code=429, detail="Trop de requêtes.")

    client.app.dependency_overrides[national_calc_rate_limit] = _limited  # type: ignore[attr-defined]
    response = client.post("/simulateur-national/calcul", data=FORM)
    assert response.status_code == 429


# --------------------------------------------------------------------------- #
# 3.5 Computable mobilisation statuses and messages unchanged
# --------------------------------------------------------------------------- #
def _mobilisation(
    monkeypatch: pytest.MonkeyPatch, participant_count: int, scenario_rate: Decimal
):  # type: ignore[no-untyped-def]
    row = SimpleNamespace(
        participant_count=participant_count,
        calculated_at=datetime(2025, 1, 1, tzinfo=UTC),
        model_version="v-test",
    )

    class _Agg:
        def __init__(self, session: object) -> None: ...

        async def latest(self):  # type: ignore[no-untyped-def]
            return row

    monkeypatch.setattr(svc_module, "AggregationService", _Agg)
    service = NationalSimulationService(_Provider(bootstrap_snapshot()), session=object())  # type: ignore[arg-type]
    return _run(service.mobilisation(scenario_rate))


def test_mobilisation_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.5**"""
    population = reference_population(bootstrap_snapshot()).value
    count = int(population / 10)  # 10 % declared
    gap = _mobilisation(monkeypatch, count, D("12"))
    assert gap.status == "ok"
    assert gap.participants == count
    assert gap.declared_rate == D("10.00")
    assert gap.gap_pp == D("2.00")
    assert gap.message == "Taux de mobilisation calculé à partir d'engagements déclaratifs non vérifiés."
    assert gap.model_version == "v-test"


def test_mobilisation_suppressed(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.5**"""
    gap = _mobilisation(monkeypatch, 1, D("10"))
    assert gap.status == "suppressed"
    assert gap.declared_rate is None and gap.gap_pp is None and gap.participants is None
    assert gap.message == (
        "Cohorte trop petite : aucun chiffre de mobilisation n'est publié. "
        "(Engagements déclaratifs non vérifiés.)"
    )


def test_mobilisation_inconsistent(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.5**"""
    population = reference_population(bootstrap_snapshot()).value
    gap = _mobilisation(monkeypatch, int(population) * 2, D("10"))
    assert gap.status == "inconsistent"
    assert gap.declared_rate is None and gap.gap_pp is None
    assert gap.message == (
        "Données incohérentes : le taux déclaré dépasse 100 %. "
        "(Engagements déclaratifs non vérifiés.)"
    )


def test_mobilisation_no_aggregate_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """**Validates: Requirements 3.5**"""

    class _Agg:
        def __init__(self, session: object) -> None: ...

        async def latest(self):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(svc_module, "AggregationService", _Agg)
    service = NationalSimulationService(_Provider(bootstrap_snapshot()), session=object())  # type: ignore[arg-type]
    gap = _run(service.mobilisation(D("10")))
    assert gap.status == "unavailable"
    assert gap.message == "Mobilisation non disponible : aucun agrégat n'a encore été calculé."
