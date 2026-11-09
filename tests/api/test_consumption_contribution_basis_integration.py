"""Tests d'intégration — contribution de consommation et sa base affichée.

Spec : ``.kiro/specs/simulateur-consumption-contribution-fix`` (tâche 3.4).

**Validates: Requirements 2.1, 2.2, 3.3**
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import net_balance
from app.core.database import get_session
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web.formatting import format_eur_fr

pytestmark = pytest.mark.api

_URL = "/api/v1/net-balance/calculate"


class _Session:
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def commit(self) -> None:
        return None


@pytest.fixture(scope="module")
def api() -> TestClient:
    app = FastAPI()
    app.include_router(net_balance.router, prefix="/api/v1/net-balance")
    app.dependency_overrides[get_session] = lambda: _Session()
    app.dependency_overrides[net_balance.get_net_balance_service] = lambda: (
        NetBalanceService(BootstrapSnapshotProvider())
    )
    return TestClient(app)


@pytest.fixture(scope="module")
def site() -> TestClient:
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def codes() -> list[str]:
    snapshot = asyncio.run(BootstrapSnapshotProvider().get_snapshot())
    return list(snapshot.category_codes)


def _conso(
    api: TestClient, codes: list[str], reduction: str = "0", adults: int = 1
) -> dict[str, Any]:
    body = {
        "consommation": {
            "reductions": {code: reduction for code in codes},
            "adultes": adults,
        }
    }
    response = api.post(_URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()["consommation"]


def test_default_request_gives_605_91_with_basis(
    api: TestClient, codes: list[str]
) -> None:
    conso = _conso(api, codes)

    assert Decimal(conso["total"]) == Decimal("605.91")
    assert conso["total_fr"] == format_eur_fr(Decimal("605.91"), places=2)
    assert Decimal(conso["depense_restante"]) > 0
    assert conso["depense_restante_fr"] == format_eur_fr(
        Decimal(conso["depense_restante"]), places=2
    )


def test_pages_contain_sub_label_and_caption(site: TestClient) -> None:
    response = site.get("/simulateur")
    assert response.status_code == 200
    html = response.text

    assert "resultat.consommation.depense_restante_fr" in html
    assert "Taxes payées sur votre dépense restante" in html
    assert "Différent de l'impact sur les finances publiques" in html
    # Légende de la carte d'impact public, distincte des taxes du bilan.
    assert "taxes payées" in html.lower()
    assert "Impact estimé sur les finances publiques" in html


def test_changing_reduction_updates_contribution_and_basis(
    api: TestClient, codes: list[str]
) -> None:
    base = _conso(api, codes, "0")
    reduced = _conso(api, codes, "40")

    assert Decimal(reduced["depense_restante"]) < Decimal(base["depense_restante"])
    assert Decimal(reduced["total"]) < Decimal(base["total"])
    assert reduced["depense_restante_fr"] != base["depense_restante_fr"]


def test_changing_adults_updates_contribution_and_basis(
    api: TestClient, codes: list[str]
) -> None:
    one = _conso(api, codes, "0", adults=1)
    two = _conso(api, codes, "0", adults=2)

    assert Decimal(two["depense_restante"]) > Decimal(one["depense_restante"])
    assert Decimal(two["total"]) > Decimal(one["total"])
    assert two["depense_restante_fr"] != one["depense_restante_fr"]


def test_full_reduction_gives_zero_contribution_and_basis(
    api: TestClient, codes: list[str]
) -> None:
    conso = _conso(api, codes, "100")

    assert Decimal(conso["total"]) == 0
    assert Decimal(conso["depense_restante"]) == 0
