"""Performance d'agrégation sur 100 000 citoyens générés avec une graine.

**Validates: Requirements 18.8, 18.9, 18.10, 18.11**

Chaque opération (agrégation du scénario national, agrégation régionale) doit se
terminer en moins de ``THRESHOLD_SECONDS`` secondes. En cas de dépassement, le message
d'échec indique l'opération, la durée mesurée et le seuil.

Ces tests portent le marqueur ``perf`` : ils sont exclus de ``pytest`` par défaut et
lancés par ``make perf-test``. Ils exigent une base PostgreSQL (``TEST_DATABASE_URL``),
sinon ils sont ignorés. Toutes les données portent le préfixe ``loadtest+<graine>-`` et
sont supprimées en fin de test (succès ou échec), reliquat vérifié.
"""

from __future__ import annotations

import random
import time
from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal

import pytest
import pytest_asyncio
from app.economics.defaults import bootstrap_snapshot
from app.models.aggregation import AggregatedImpact
from app.models.citizen_commitment import CitizenCommitment
from app.models.consumption_category import ConsumptionCategory
from app.models.user import User
from app.reference.regions import load_regions
from app.services.aggregation_service import AggregationService
from app.services.regional_aggregation_service import RegionalAggregationService
from loadtest.data import EMAIL_DOMAIN, prefix_for
from sqlalchemy import delete, func, insert, select

pytestmark = pytest.mark.perf

CITIZENS = 100_000
SEED = 20240518
THRESHOLD_SECONDS = 60.0
CHUNK = 10_000


def _assert_within_threshold(operation: str, elapsed: float) -> None:
    assert elapsed < THRESHOLD_SECONDS, (
        f"Opération « {operation} » trop lente : {elapsed:.2f} s "
        f"(seuil : {THRESHOLD_SECONDS:.0f} s)"
    )


async def _populate(session, today: date) -> None:
    """Insère ``CITIZENS`` citoyens consentants et leurs engagements, de façon déterministe."""
    prefix = prefix_for(SEED)
    rng = random.Random(f"perf:{SEED}")
    regions = [r.code for r in load_regions().regions]
    category_ids = list((await session.scalars(select(ConsumptionCategory.id))).all())
    start, end = today - timedelta(days=30), today + timedelta(days=335)

    for offset in range(0, CITIZENS, CHUNK):
        indexes = range(offset, min(offset + CHUNK, CITIZENS))
        await session.execute(
            insert(User),
            [
                {
                    "email": f"{prefix}{i:06d}@{EMAIL_DOMAIN}",
                    "password_hash": "x",
                    "display_name": f"{prefix}user{i:06d}",
                    "is_active": True,
                    "is_verified": False,
                    "country": "FR",
                    # ~5 % sans région valide : exerce le groupe « sans Région ».
                    "region": rng.choice(regions) if rng.random() > 0.05 else None,
                    "household_adults": rng.randint(1, 4),
                    "role": "citizen",
                    "consents": {"aggregate_commitments": True},
                }
                for i in indexes
            ],
        )
    await session.commit()

    ids = (await session.scalars(select(User.id).where(User.email.like(f"{prefix}%")))).all()
    assert len(ids) == CITIZENS
    rows = []
    for user_id in ids:
        for category_id in rng.sample(category_ids, k=rng.randint(1, min(3, len(category_ids)))):
            rows.append(
                {
                    "user_id": user_id,
                    "category_id": category_id,
                    "reduction_percentage": Decimal(rng.randint(5, 60)),
                    "start_date": start,
                    "end_date": end,
                    "status": "active",
                }
            )
    for offset in range(0, len(rows), CHUNK):
        await session.execute(insert(CitizenCommitment), rows[offset : offset + CHUNK])
    await session.commit()


async def _cleanup(session) -> list[str]:
    """Supprime les données préfixées et retourne le reliquat éventuel."""
    prefix = prefix_for(SEED)
    await session.rollback()
    await session.execute(delete(AggregatedImpact))  # agrégats produits par ce test
    await session.execute(delete(User).where(User.email.like(f"{prefix}%")))
    await session.commit()
    left = await session.scalar(
        select(func.count()).select_from(User).where(User.email.like(f"{prefix}%"))
    )
    return [f"{prefix}* : {left} comptes restants"] if left else []


@pytest_asyncio.fixture
async def citizens(econ_session) -> AsyncIterator[object]:
    session = econ_session
    try:
        await _populate(session, date.today())
        yield session
    finally:
        remaining = await _cleanup(session)
        assert not remaining, f"Nettoyage incomplet : {remaining}"


async def test_national_scenario_aggregation_under_threshold(citizens) -> None:
    snapshot = bootstrap_snapshot()
    service = AggregationService(citizens)

    started = time.perf_counter()
    row = await service.refresh(snapshot)
    await citizens.commit()
    elapsed = time.perf_counter() - started

    _assert_within_threshold("agrégation du scénario national", elapsed)
    assert row.participant_count == CITIZENS


async def test_regional_aggregation_under_threshold(citizens) -> None:
    snapshot = bootstrap_snapshot()
    service = RegionalAggregationService(citizens)

    started = time.perf_counter()
    report = await service.refresh(snapshot)
    await citizens.commit()
    elapsed = time.perf_counter() - started

    _assert_within_threshold("agrégation régionale", elapsed)
    assert not report.failed
    assert len(report.succeeded) == len(load_regions().regions)
