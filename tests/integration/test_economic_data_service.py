"""Economic data management on a real PostgreSQL (skipped when unavailable)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.economics.direct import compute_direct_impact
from app.economics.personal import compute_personal_savings
from app.models.audit import AuditLog
from app.models.user import User
from app.services.economic_data_service import (
    DatasetMetadata,
    DatasetValidationError,
    EconomicDataService,
    ImmutableVersionError,
    PublishError,
)
from sqlalchemy import select

from tests.economic.helpers import CSV_HEADER, bootstrap_vat_csv

pytestmark = pytest.mark.integration

D = Decimal
META = DatasetMetadata(
    name="vat-splits",
    source="Test",
    source_url="https://example.org/vat",
    reference_year=2026,
)


async def _admin(session) -> int:
    user = User(
        email="admin@example.org", password_hash="x", display_name="Admin", role="data_admin"
    )
    session.add(user)
    await session.flush()
    return user.id


def _simulate(snapshot, scenario: str = "central") -> Decimal:
    reductions = {c.code: D("100") for c in snapshot.categories}
    savings = compute_personal_savings(snapshot, reductions)
    return compute_direct_impact(snapshot, savings, scenario).vat


async def test_import_validate_publish_and_snapshot(econ_session) -> None:
    service = EconomicDataService(econ_session)
    admin = await _admin(econ_session)
    dataset = await service.import_csv(META, bootstrap_vat_csv(), admin)
    assert dataset.version == 1 and dataset.validation_status == "validated"
    assert len(dataset.checksum) == 64

    version = await service.create_model_version("v1", [dataset.id], "first", admin)
    assert version.status == "draft"
    published = await service.publish(version.id, admin)
    assert published.status == "published" and published.published_at is not None

    latest = await service.get_latest_published()
    assert latest is not None
    _, snapshot = latest
    assert snapshot.model_version == "v1"
    assert snapshot.dataset_versions == ("vat-splits@v1",)
    assert set(snapshot.vat_profiles) == set(snapshot.category_codes)

    actions = (await econ_session.execute(select(AuditLog.action))).scalars().all()
    assert {"economic_dataset.import", "model_version.create", "model_version.publish"} <= set(
        actions
    )


async def test_invalid_csv_is_rejected_and_nothing_is_stored(econ_session) -> None:
    service = EconomicDataService(econ_session)
    with pytest.raises(DatasetValidationError):
        await service.import_csv(
            META, CSV_HEADER + "restaurants,vat_split,0.10,0.5,0.5,0.5,share,high,\n", None
        )
    assert await service.list_datasets() == []


async def test_unknown_category_is_rejected(econ_session) -> None:
    service = EconomicDataService(econ_session)
    with pytest.raises(DatasetValidationError):
        await service.import_csv(
            META, CSV_HEADER + "yachts,vat_split,0.20,1,1,1,share,high,\n", None
        )


async def test_publish_requires_complete_parameters(econ_session) -> None:
    service = EconomicDataService(econ_session)
    partial = CSV_HEADER + "clothing,vat_split,0.20,1,1,1,share,high,\n"
    dataset = await service.import_csv(META, partial, None)
    version = await service.create_model_version("partial", [dataset.id], "", None)
    with pytest.raises(PublishError, match="restaurants"):
        await service.publish(version.id, None)


async def test_published_version_cannot_be_published_again(econ_session) -> None:
    service = EconomicDataService(econ_session)
    dataset = await service.import_csv(META, bootstrap_vat_csv(), None)
    version = await service.create_model_version("v1", [dataset.id], "", None)
    await service.publish(version.id, None)
    with pytest.raises(ImmutableVersionError):
        await service.publish(version.id, None)


async def test_dataset_versions_increment(econ_session) -> None:
    service = EconomicDataService(econ_session)
    first = await service.import_csv(META, bootstrap_vat_csv(), None)
    second = await service.import_csv(META, bootstrap_vat_csv(True), None)
    assert (first.version, second.version) == (1, 2)
    assert first.checksum != second.checksum


async def test_historical_simulation_stays_reproducible(econ_session) -> None:
    service = EconomicDataService(econ_session)
    d1 = await service.import_csv(META, bootstrap_vat_csv(), None)
    v1 = await service.publish(
        (await service.create_model_version("v1", [d1.id], "", None)).id, None
    )
    before = _simulate(await service.get_snapshot(v1.id))

    d2 = await service.import_csv(META, bootstrap_vat_csv(True), None)
    v2 = await service.publish(
        (await service.create_model_version("v2", [d2.id], "", None)).id, None
    )

    assert _simulate(await service.get_snapshot(v1.id)) == before  # unchanged
    assert _simulate(await service.get_snapshot(v2.id)) != before  # new model differs
    latest = await service.get_latest_published()
    assert latest is not None and latest[0].label == "v2"


async def test_compare_versions(econ_session) -> None:
    service = EconomicDataService(econ_session)
    d1 = await service.import_csv(META, bootstrap_vat_csv(), None)
    v1 = await service.create_model_version("v1", [d1.id], "", None)
    d2 = await service.import_csv(META, bootstrap_vat_csv(True), None)
    v2 = await service.create_model_version("v2", [d2.id], "", None)
    diff = await service.compare(v1.id, v2.id)
    assert diff.changed and all(k.startswith("restaurants/") for k in diff.changed)
    assert not diff.added and not diff.removed


async def test_database_enforces_published_at_consistency(econ_session) -> None:
    from app.models.economic_dataset import ModelVersion
    from sqlalchemy.exc import IntegrityError

    econ_session.add(ModelVersion(label="bad", status="published", published_at=None))
    with pytest.raises(IntegrityError):
        await econ_session.flush()
