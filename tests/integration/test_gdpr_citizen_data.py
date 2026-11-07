"""GDPR: export and deletion of Partipolia citizen data (real PostgreSQL)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.api.v1.commitments import export_my_data
from app.models.citizen_commitment import CitizenCommitment
from app.models.simulation import EconomicSimulation, SimulationResult
from app.models.user import User
from app.schemas.auth import UserPublic
from app.schemas.commitment import CommitmentCreate
from app.schemas.simulation import SimulationRequest
from app.services.commitment_service import CONSENT_KEY, CommitmentService
from app.services.privacy_service import PrivacyService
from app.services.simulation_service import SimulationService
from sqlalchemy import delete, func, select

pytestmark = pytest.mark.integration

D = Decimal


async def _citizen_with_data(session) -> User:
    user = User(
        email="citizen@example.org",
        password_hash="x",
        display_name="Pseudo",
        region="11",
        household_adults=2,
        consents={CONSENT_KEY: True},
    )
    session.add(user)
    await session.flush()
    await CommitmentService(session).create(
        user.id,
        CommitmentCreate(
            category_code="restaurants", reduction_percentage=D("40"), duration_months=6
        ),
    )
    await SimulationService(session=session).calculate(
        SimulationRequest(reductions={"clothing": D("30")}, save=True), user.id
    )
    return user


def _public(user: User) -> UserPublic:
    return UserPublic.model_validate(
        {
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "is_active": True,
            "is_verified": False,
            "is_admin": False,
            "role": "citizen",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )


async def test_export_contains_all_partipolia_data(econ_session) -> None:
    user = await _citizen_with_data(econ_session)
    export = await export_my_data(econ_session, _public(user))
    assert export.email == "citizen@example.org" and export.pseudonym == "Pseudo"
    assert export.profile.region == "11" and export.profile.household_adults == 2
    assert export.profile.consent_aggregation is True
    assert [c.category_code for c in export.commitments] == ["restaurants"]
    assert export.commitments[0].reduction_percentage == D("40")
    assert len(export.saved_simulations) == 1
    assert set(export.saved_simulations[0]["input"]) == {"reductions", "adults", "horizon"}
    assert "password" not in export.model_dump_json().lower()


async def test_export_only_contains_the_requesters_data(econ_session) -> None:
    user = await _citizen_with_data(econ_session)
    other = User(email="other@example.org", password_hash="x", display_name="Other")
    econ_session.add(other)
    await econ_session.flush()
    export = await export_my_data(econ_session, _public(other))
    assert export.commitments == [] and export.saved_simulations == []
    assert user.id != other.id


async def test_account_deletion_erases_commitments_and_saved_simulations(econ_session) -> None:
    user = await _citizen_with_data(econ_session)
    other = User(email="other@example.org", password_hash="x", display_name="Other")
    econ_session.add(other)
    await econ_session.flush()
    await CommitmentService(econ_session).create(
        other.id,
        CommitmentCreate(category_code="leisure", reduction_percentage=D("10"), duration_months=1),
    )

    # PrivacyService.delete_account ends with this very statement.
    await econ_session.execute(delete(User).where(User.id == user.id))
    await econ_session.flush()

    assert (
        await econ_session.scalar(
            select(func.count())
            .select_from(EconomicSimulation)
            .where(EconomicSimulation.user_id == user.id)
        )
        == 0
    )
    assert await econ_session.scalar(select(func.count()).select_from(SimulationResult)) == 0
    remaining = (await econ_session.execute(select(CitizenCommitment))).scalars().all()
    assert [c.user_id for c in remaining] == [other.id]  # only the other citizen's data remains


def test_retention_policy_covers_partipolia_data() -> None:
    categories = {rule.category for rule in PrivacyService.retention_policy().rules}
    assert {
        "Engagements citoyens (déclaratifs)",
        "Simulations enregistrées",
        "Agrégats nationaux",
    } <= categories
