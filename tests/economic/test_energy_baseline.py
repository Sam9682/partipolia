"""Unit tests for the energy baseline line."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from app.economics.energy_baseline import EnergyConfigurationError, compute_energy_baseline
from app.economics.parameters import ENERGY_BASELINE_REQUIREMENTS
from app.economics.types import Confidence, ModelSnapshot, ParameterRange

_VALUES = {
    "avg_fuel_l_per_adult": "470",
    "avg_heating_oil_l_per_adult": "80",
    "fuel_price_ttc_per_l": "1.75",
    "fuel_excise_per_l": "0.60",
    "heating_oil_price_ttc_per_l": "1.10",
    "heating_oil_excise_per_l": "0.15",
    "fuel_vat_rate": "0.20",
}


def _snapshot() -> ModelSnapshot:
    params = {
        (name, None, "*"): ParameterRange(
            Decimal(v), Decimal(v), Decimal(v), Confidence.PROVISIONAL
        )
        for name, v in _VALUES.items()
    }
    return ModelSnapshot("test", (), (), parameters=params)


def test_central_values() -> None:
    result = compute_energy_baseline(_snapshot())
    assert result.fuel.spend_ttc == Decimal("822.50")
    assert result.fuel.excise == Decimal("282.00")
    assert result.fuel.vat == Decimal("137.08")
    assert result.heating_oil.spend_ttc == Decimal("88.00")
    assert result.heating_oil.excise == Decimal("12.00")
    assert result.heating_oil.vat == Decimal("14.67")
    assert result.total.spend_ttc == Decimal("910.50")


@pytest.mark.parametrize("name", ENERGY_BASELINE_REQUIREMENTS)
def test_missing_parameter_is_named(name: str) -> None:
    snap = _snapshot()
    params = {k: v for k, v in snap.parameters.items() if k[0] != name}
    with pytest.raises(EnergyConfigurationError, match=name):
        compute_energy_baseline(replace(snap, parameters=params))
