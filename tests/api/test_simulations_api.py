"""Anonymous simulator API: calculation, validation, determinism."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.api.deps import get_current_user_optional
from app.api.v1 import simulations
from app.core.middleware import register_exception_handlers
from app.services.simulation_service import SimulationService
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

SPEC_EXAMPLE = {
    "restaurants": "100",
    "clothing": "50",
    "leisure": "75",
    "hotels_holidays": "50",
    "electronics": "30",
    "digital_subscriptions": "100",
}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(simulations.router, prefix="/api/v1/simulations")
    app.dependency_overrides[simulations.get_simulation_service] = lambda: SimulationService()
    app.dependency_overrides[get_current_user_optional] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict) -> object:
    return client.post("/api/v1/simulations/calculate", json=body)


def test_spec_example_without_authentication(client: TestClient) -> None:
    response = _post(client, {"reductions": SPEC_EXAMPLE})
    assert response.status_code == 200
    data = response.json()
    assert data["personal"]["annual_savings"] == "3015.00"
    assert data["personal"]["monthly_savings"] == "251.25"
    assert data["personal"]["original_expenditure"] == "4170.00"
    assert data["model_version"]
    assert len(data["categories"]) == 6
    assert data["disclaimers"]


def test_money_is_serialized_as_strings_not_floats(client: TestClient) -> None:
    data = _post(client, {"reductions": SPEC_EXAMPLE}).json()
    assert isinstance(data["personal"]["annual_savings"], str)
    assert isinstance(data["direct"]["vat"]["central"], str)


def test_direct_vat_range_is_ordered_and_separate_from_savings(client: TestClient) -> None:
    data = _post(client, {"reductions": SPEC_EXAMPLE}).json()
    vat = {k: Decimal(v) for k, v in data["direct"]["vat"].items()}
    assert vat["low"] <= vat["central"] <= vat["high"]
    assert vat["central"] < Decimal(data["personal"]["annual_savings"])
    ht = Decimal(data["direct"]["turnover_ht"]["central"])
    assert ht + vat["central"] == Decimal(data["personal"]["annual_savings"])
    assert data["direct"]["confidence"] == "provisional"


def test_zero_reduction(client: TestClient) -> None:
    data = _post(client, {"reductions": {}}).json()
    assert data["personal"]["annual_savings"] == "0.00"
    assert data["direct"]["vat"]["central"] == "0.00"


def test_household_of_two_doubles_savings(client: TestClient) -> None:
    one = _post(client, {"reductions": SPEC_EXAMPLE, "adults": 1}).json()
    two = _post(client, {"reductions": SPEC_EXAMPLE, "adults": 2}).json()
    assert Decimal(two["personal"]["annual_savings"]) == 2 * Decimal(
        one["personal"]["annual_savings"]
    )


def test_deterministic(client: TestClient) -> None:
    assert (
        _post(client, {"reductions": SPEC_EXAMPLE}).json()
        == _post(client, {"reductions": SPEC_EXAMPLE}).json()
    )


@pytest.mark.parametrize(
    "body",
    [
        {"reductions": {"restaurants": "101"}},
        {"reductions": {"restaurants": "-1"}},
        {"reductions": {"restaurants": "abc"}},
        {"reductions": {"restaurants": "NaN"}},
        {"reductions": {"restaurants": "10.00001"}},
        {"reductions": {"yachts": "10"}},
        {"reductions": {}, "adults": 0},
        {"reductions": {}, "adults": 11},
        {"reductions": {}, "unexpected": 1},
    ],
)
def test_invalid_input_is_rejected(client: TestClient, body: dict) -> None:
    response = _post(client, body)
    # Project convention: validation failures are normalized to 400 VALIDATION_ERROR.
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_rate_limit_dependency_is_attached() -> None:
    route = next(r for r in simulations.router.routes if r.path == "/calculate")
    assert len(route.dependencies) == 1  # default_rate_limit("simulate")


def _ranges_ordered(r: dict) -> bool:
    return Decimal(r["low"]) <= Decimal(r["central"]) <= Decimal(r["high"])


def test_full_fiscal_breakdown_is_returned_with_ranges(client: TestClient) -> None:
    data = _post(client, {"reductions": SPEC_EXAMPLE}).json()
    fiscal = data["fiscal"]
    assert fiscal["currency"] == "EUR" and fiscal["period"] == "année"
    assert all(fiscal["modules"].values())
    for key in (
        "direct",
        "indirect",
        "induced",
        "gross_revenue_loss",
        "additional_public_expenditure",
        "net_public_impact",
        "reduced_turnover",
        "reduced_value_added",
    ):
        assert _ranges_ordered(fiscal[key]), key
    # Distinct indicators: turnover is not value added and neither is tax revenue.
    assert fiscal["reduced_turnover"]["central"] != fiscal["reduced_value_added"]["central"]
    assert Decimal(fiscal["gross_revenue_loss"]["central"]) < Decimal(
        data["personal"]["annual_savings"]
    )
    assert fiscal["confidence"] == "provisional" and fiscal["provisional_parameters"]
    assert "estimations" in " ".join(data["disclaimers"])
    assert data["model_version"] and data["dataset_versions"]


def test_categories_contributions_add_up_to_the_total(client: TestClient) -> None:
    data = _post(client, {"reductions": SPEC_EXAMPLE}).json()
    total = sum(Decimal(c["net_public_impact"]["central"]) for c in data["categories"])
    assert abs(total - Decimal(data["fiscal"]["net_public_impact"]["central"])) <= Decimal("0.10")


def test_horizon_changes_induced_effects(client: TestClient) -> None:
    short = _post(client, {"reductions": SPEC_EXAMPLE, "horizon": "short"}).json()
    long = _post(client, {"reductions": SPEC_EXAMPLE, "horizon": "long"}).json()
    assert Decimal(short["fiscal"]["induced"]["central"]) < Decimal(
        long["fiscal"]["induced"]["central"]
    )
    assert _post(client, {"reductions": {}, "horizon": "never"}).status_code == 400


def test_top_sectors_are_reported(client: TestClient) -> None:
    data = _post(client, {"reductions": {"restaurants": "100"}}).json()
    top = data["fiscal"]["top_sectors"]
    assert top and top[0]["code"] == "IZ"  # accommodation and food services come first
