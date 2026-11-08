"""Paramètres de positionnement du CSV du bilan net (Exigences 1.1, 2.4)."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from app.economics.net_balance.parameters import (
    NetBalanceParametersError,
    build_parameters,
    load_net_balance_parameters,
)
from app.economics.types import Confidence


def test_positioning_loaded_from_csv() -> None:
    params = load_net_balance_parameters()
    assert params.positioning.neutral_level == Decimal("30900")
    assert params.positioning.net_transfer_rate == Decimal("0.35")


def test_provisional_rows_include_transfer_rate() -> None:
    ids = {r.id for r in load_net_balance_parameters().provisional_rows()}
    assert "positionnement.taux_transfert_net" in ids
    assert "positionnement.niveau_vie_neutre" not in ids


def test_missing_positioning_row_is_rejected() -> None:
    rows = [
        r
        for r in load_net_balance_parameters().rows
        if r.id != "positionnement.niveau_vie_neutre"
    ]
    with pytest.raises(NetBalanceParametersError, match="positionnement.niveau_vie_neutre"):
        build_parameters(rows)


def test_non_positive_neutral_level_is_rejected() -> None:
    rows = [
        replace(r, value=Decimal(0)) if r.id == "positionnement.niveau_vie_neutre" else r
        for r in load_net_balance_parameters().rows
    ]
    with pytest.raises(NetBalanceParametersError, match="strictement positive"):
        build_parameters(rows)


def test_neutral_level_confidence_is_not_provisional() -> None:
    row = load_net_balance_parameters().positioning.neutral_level_row
    assert row.confidence == Confidence.MEDIUM
