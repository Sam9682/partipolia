"""Test d'exploration de la condition de bug — base de la contribution de consommation.

Spec bugfix : ``.kiro/specs/simulateur-consumption-contribution-fix`` (tâche 1).

**Property 1: Bug Condition** — Le bilan net affiche « Contribution de consommation
(TVA et accises) » (ex. 605,91 €) avec toutes les réductions à 0 %, sans indiquer la
dépense restante sur laquelle elle est calculée, et sans la distinguer de
« l'Impact estimé sur les finances publiques ».

**CRITIQUE** : ce test DOIT ÉCHOUER sur le code NON corrigé — l'échec confirme que le
bug existe. Il encode le comportement attendu et PASSERA après le correctif (tâche 3.1),
sans être réécrit.

**Validates: Requirements 1.1, 1.2, 2.1, 2.2**

Contre-exemples attendus sur le code non corrigé : ``consommation`` n'a pas de champ
``depense_restante`` / ``depense_restante_fr`` et les gabarits ne mentionnent ni la base
ni la distinction avec l'impact sur les finances publiques.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.v1 import net_balance
from app.core.database import get_session
from app.economics.personal import compute_personal_savings
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web import router as web

pytestmark = pytest.mark.api

_CALCULATE_URL = "/api/v1/net-balance/calculate"
_PARTIALS = web.TEMPLATES_DIR / "public" / "partials"
_BILAN_PARTIAL: Path = _PARTIALS / "net_balance.html"
_CONSUMPTION_PARTIAL: Path = _PARTIALS / "simulator_consumption.html"

_SETTINGS = settings(
    max_examples=10,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


class _Session:
    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        return None

    async def commit(self) -> None:
        return None


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(net_balance.router, prefix="/api/v1/net-balance")
    app.dependency_overrides[get_session] = lambda: _Session()
    app.dependency_overrides[net_balance.get_net_balance_service] = lambda: (
        NetBalanceService(BootstrapSnapshotProvider())
    )
    return TestClient(app)


def _snapshot() -> Any:
    return asyncio.run(BootstrapSnapshotProvider().get_snapshot())


def _reference_total(adults: int = 1) -> Decimal:
    """Dépense de référence totale (réductions 0 % => tout reste dépensé)."""
    snapshot = _snapshot()
    full = {code: Decimal(100) for code in snapshot.category_codes}
    return compute_personal_savings(snapshot, full, adults, None).avoided_expenditure


def _consommation(body: dict[str, Any]) -> dict[str, Any]:
    response = _client().post(_CALCULATE_URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _default_body(adults: int = 1) -> dict[str, Any]:
    snapshot = _snapshot()
    return {
        "consommation": {
            "reductions": {code: "0" for code in snapshot.category_codes},
            "adultes": adults,
        }
    }


# --------------------------------------------------------------------------- #
# Cas 1 : la réponse expose la dépense restante (état par défaut)              #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(adults=st.integers(1, 3))
def test_response_exposes_remaining_expenditure_basis(adults: int) -> None:
    data = _consommation(_default_body(adults))
    conso = data["consommation"]

    # Échoue sur code non corrigé : champ absent.
    assert "depense_restante" in conso, "consommation sans depense_restante"
    assert "depense_restante_fr" in conso, "consommation sans depense_restante_fr"
    assert Decimal(conso["depense_restante"]) == _reference_total(adults)

    # Le montant calculé reste TVA + accises + TVA carburant, distinct de l'impact public.
    total = Decimal(conso["tva"]) + Decimal(conso["accises_carburant"]) + Decimal(
        conso["tva_carburant"]
    )
    assert Decimal(conso["total"]) == total
    assert Decimal(conso["total"]) > 0


# --------------------------------------------------------------------------- #
# Cas 2 : gabarit du bilan, base affichée et distinction                       #
# --------------------------------------------------------------------------- #
def test_bilan_row_references_remaining_expenditure_basis() -> None:
    html = _BILAN_PARTIAL.read_text(encoding="utf-8")
    assert "depense_restante_fr" in html, "le bilan n'affiche pas la dépense restante"


def test_bilan_and_fiscal_card_state_difference_with_public_finances_impact() -> None:
    bilan = _BILAN_PARTIAL.read_text(encoding="utf-8").lower()
    fiscal = _CONSUMPTION_PARTIAL.read_text(encoding="utf-8").lower()
    assert "finances publiques" in bilan, "bilan sans distinction avec l'impact public"
    assert "bilan" in fiscal and "taxes payées" in fiscal, (
        "carte d'impact public sans distinction avec les taxes du bilan"
    )


# --------------------------------------------------------------------------- #
# Cas 3 : montants tous à 0 => base nulle                                      #
# --------------------------------------------------------------------------- #
def test_zero_amounts_give_zero_basis() -> None:
    snapshot = _snapshot()
    body = _default_body()
    body["consommation"]["reference_overrides"] = {
        code: "0" for code in snapshot.category_codes
    }
    conso = _consommation(body)["consommation"]

    assert conso["total"] in ("0", "0.00", "0E-2") or Decimal(conso["total"]) == 0
    # Échoue sur code non corrigé : champ absent.
    assert "depense_restante" in conso, "consommation sans depense_restante"
    assert Decimal(conso["depense_restante"]) == 0
