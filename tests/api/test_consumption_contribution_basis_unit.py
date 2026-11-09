"""Tests unitaires — base (dépense restante) de la contribution de consommation.

Spec : ``.kiro/specs/simulateur-consumption-contribution-fix`` (tâche 3.4).

**Validates: Requirements 2.1, 3.1, 3.2, 3.3**
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import pytest

from app.economics.net_balance import compute_net_balance, load_net_balance_parameters
from app.economics.net_balance.consumption import compute_consumption_contribution
from app.economics.net_balance.validation import parse_input
from app.schemas.net_balance import NetBalanceRequest
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web.formatting import format_eur_fr

pytestmark = pytest.mark.api

_CENT = Decimal("0.01")


def _snapshot() -> Any:
    return asyncio.run(BootstrapSnapshotProvider().get_snapshot())


def _expected_remaining(
    snapshot: Any,
    reductions: dict[str, Decimal],
    adults: int,
    overrides: dict[str, Decimal] | None = None,
) -> Decimal:
    total = Decimal(0)
    for category in snapshot.categories:
        reference = (overrides or {}).get(
            category.code, category.reference_annual_expenditure
        )
        r = reductions.get(category.code, Decimal(0))
        total += reference * adults * (Decimal(100) - r) / Decimal(100)
    return total


def _assert_close(actual: Decimal, expected: Decimal, categories: int) -> None:
    # Chaque catégorie est arrondie au centime dans le moteur.
    assert abs(actual - expected) <= _CENT * categories


@pytest.mark.parametrize(
    ("reduction", "adults"),
    [("0", 1), ("40", 1), ("25", 2), ("0", 3)],
)
def test_remaining_expenditure_matches_reference_times_inverse_reduction(
    reduction: str, adults: int
) -> None:
    snapshot = _snapshot()
    reductions = {code: Decimal(reduction) for code in snapshot.category_codes}

    result = compute_consumption_contribution(snapshot, reductions, adults, None)

    expected = _expected_remaining(snapshot, reductions, adults)
    _assert_close(result.remaining_expenditure, expected, len(snapshot.categories))
    assert result.total == result.vat + result.fuel_excise + result.fuel_vat


def test_remaining_expenditure_uses_reference_overrides() -> None:
    snapshot = _snapshot()
    code = snapshot.category_codes[0]
    overrides = {code: Decimal("1000")}
    reductions = {c: Decimal(50) for c in snapshot.category_codes}

    result = compute_consumption_contribution(snapshot, reductions, 1, overrides)

    expected = _expected_remaining(snapshot, reductions, 1, overrides)
    _assert_close(result.remaining_expenditure, expected, len(snapshot.categories))


def test_remaining_expenditure_is_zero_for_zero_amounts() -> None:
    snapshot = _snapshot()
    overrides = {code: Decimal(0) for code in snapshot.category_codes}
    reductions = {code: Decimal(0) for code in snapshot.category_codes}

    result = compute_consumption_contribution(snapshot, reductions, 1, overrides)

    assert result.remaining_expenditure == 0
    assert result.total == 0


def test_remaining_expenditure_is_zero_for_full_reductions() -> None:
    snapshot = _snapshot()
    reductions = {code: Decimal(100) for code in snapshot.category_codes}

    result = compute_consumption_contribution(snapshot, reductions, 2, None)

    assert result.remaining_expenditure == 0
    assert result.total == 0


def test_build_response_fills_remaining_expenditure_fields() -> None:
    snapshot = _snapshot()
    request = NetBalanceRequest.model_validate(
        {
            "consommation": {
                "reductions": {code: "0" for code in snapshot.category_codes},
                "adultes": 1,
            }
        }
    )
    data, errors = parse_input(request)
    assert data is not None, errors
    params = load_net_balance_parameters()
    result = compute_net_balance(data, params, snapshot)

    response = NetBalanceService._build_response(
        result, params, data.adults, data.children
    )

    basis = result.consumption.remaining_expenditure
    assert basis > 0
    assert response.consommation.depense_restante == basis
    assert response.consommation.depense_restante_fr == format_eur_fr(basis, places=2)
    assert response.consommation.total == result.consumption.total
