"""Saved simulations stay reproducible after a new model version is published."""

from __future__ import annotations

import pytest
from app.models.simulation import EconomicSimulation, SimulationResult
from app.schemas.simulation import SimulationRequest
from app.services.economic_data_service import DatasetMetadata, EconomicDataService
from app.services.simulation_service import (
    DatabaseSnapshotProvider,
    SimulationForbiddenError,
    SimulationNotFoundError,
    SimulationService,
)

from tests.economic.helpers import bootstrap_vat_csv

pytestmark = pytest.mark.integration

META = DatasetMetadata(name="vat", source="t", source_url="", reference_year=2026)
REQUEST = SimulationRequest(
    reductions={"restaurants": "100", "clothing": "50"},
    adults=2,
    save=True,  # type: ignore[arg-type]
)


async def _publish(session, label: str, alt: bool) -> int:
    service = EconomicDataService(session)
    dataset = await service.import_csv(META, bootstrap_vat_csv(alt), None)
    version = await service.create_model_version(label, [dataset.id], "", None)
    await service.publish(version.id, None)
    return version.id


def _service(session) -> SimulationService:
    return SimulationService(DatabaseSnapshotProvider(session), session)


async def test_falls_back_to_bootstrap_when_nothing_is_published(econ_session) -> None:
    response = await _service(econ_session).calculate(REQUEST)
    assert response.model_version == "bootstrap-0"
    assert response.simulation_id is not None
    stored = await econ_session.get(EconomicSimulation, response.simulation_id)
    assert stored is not None and stored.model_version_id is None


async def test_saved_simulation_is_pinned_to_its_model_version(econ_session) -> None:
    v1 = await _publish(econ_session, "v1", alt=False)
    first = await _service(econ_session).calculate(REQUEST)
    assert first.model_version == "v1" and first.dataset_versions == ["vat@v1"]

    await _publish(econ_session, "v2", alt=True)
    latest = await _service(econ_session).calculate(REQUEST.model_copy(update={"save": False}))
    assert latest.model_version == "v2"
    assert latest.direct.vat != first.direct.vat

    replay = await _service(econ_session).get(first.simulation_id, None)
    assert replay.model_version == "v1"
    assert replay.direct.vat == first.direct.vat
    assert replay.personal == first.personal
    stored = await econ_session.get(EconomicSimulation, first.simulation_id)
    assert stored is not None and stored.model_version_id == v1


async def test_results_rows_are_stored_per_category(econ_session) -> None:
    response = await _service(econ_session).calculate(REQUEST)
    stored = await econ_session.get(EconomicSimulation, response.simulation_id)
    assert stored is not None
    assert len(stored.results) == 6
    assert all(isinstance(r, SimulationResult) for r in stored.results)
    assert all(r.confidence_low <= r.net_fiscal_impact <= r.confidence_high for r in stored.results)


async def test_stored_input_contains_no_personal_data(econ_session) -> None:
    response = await _service(econ_session).calculate(REQUEST)
    stored = await econ_session.get(EconomicSimulation, response.simulation_id)
    assert stored is not None
    assert set(stored.input_payload) == {"reductions", "adults", "horizon"}


async def test_unknown_and_foreign_simulations_are_not_readable(econ_session) -> None:
    from app.models.user import User

    owner = User(email="o@example.org", password_hash="x", display_name="O")
    econ_session.add(owner)
    await econ_session.flush()
    response = await _service(econ_session).calculate(REQUEST, owner.id)

    with pytest.raises(SimulationNotFoundError):
        await _service(econ_session).get(999_999, None)
    with pytest.raises(SimulationForbiddenError):
        await _service(econ_session).get(response.simulation_id, None)
    assert (await _service(econ_session).get(response.simulation_id, owner.id)).simulation_id
