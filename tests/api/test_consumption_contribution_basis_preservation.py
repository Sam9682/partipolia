"""Préservation : contribution de consommation du bilan net (Property 2).

Spec bugfix : ``.kiro/specs/simulateur-consumption-contribution-fix`` (tâche 2).

Tests écrits observation-first sur le code NON corrigé ; ils DOIVENT passer avant
comme après le correctif. Ils capturent les montants (contribution, bilan, verdict,
impact sur les finances publiques) et les erreurs de champ, qui ne doivent pas
changer quand on ajoute la base « dépense restante » à la réponse.

* défaut : contribution 605,91 € (Exigence 3.3) ;
* dépenses toutes à 0 ou réductions toutes à 100 % : 0,00 € (3.1, 3.2) ;
* la contribution est la somme TVA + accises + TVA carburant, bornée par la valeur
  sans réduction, nulle à 100 %, et croît avec le nombre d'adultes (3.3) ;
* le bilan et le total des contributions intègrent la contribution à l'identique,
  le verdict suit le signe du bilan (3.5) ;
* ``net_public_impact`` vaut 0 sans réduction et ne dépend que des réductions (3.4) ;
* montant vide ou invalide : mêmes erreurs de champ (3.6).

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.deps import get_current_user_optional
from app.api.v1 import net_balance
from app.core.database import get_session
from app.economics.defaults import bootstrap_snapshot
from app.schemas.simulation import SimulationRequest
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider, SimulationService

pytestmark = pytest.mark.api

D = Decimal
URL = "/api/v1/net-balance/calculate"
CODES = list(bootstrap_snapshot().category_codes)
BASE_BODY: dict[str, Any] = {"revenus": [{"statut": "salarie", "revenu_net": "24000"}]}

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _client() -> TestClient:
    app = FastAPI()
    app.include_router(net_balance.router, prefix="/api/v1/net-balance")
    app.dependency_overrides[get_session] = lambda: None
    app.dependency_overrides[net_balance.get_net_balance_service] = lambda: NetBalanceService(
        BootstrapSnapshotProvider()
    )
    app.dependency_overrides[get_current_user_optional] = lambda: None
    return TestClient(app)


CLIENT = _client()


def _body(
    reductions: dict[str, str] | None = None,
    overrides: dict[str, str] | None = None,
    adults: int | None = None,
) -> dict[str, Any]:
    conso: dict[str, Any] = {}
    if reductions is not None:
        conso["reductions"] = reductions
    if overrides is not None:
        conso["reference_overrides"] = overrides
    if adults is not None:
        conso["adultes"] = adults
    body = dict(BASE_BODY)
    if conso:
        body["consommation"] = conso
    return body


def _post(body: dict[str, Any]) -> dict[str, Any]:
    response = CLIENT.post(URL, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _total(data: dict[str, Any]) -> Decimal:
    return D(data["consommation"]["total"])


def _public_impact(reductions: dict[str, str], overrides: dict[str, str], adults: int) -> Decimal:
    request = SimulationRequest(
        reductions={k: D(v) for k, v in reductions.items()},
        reference_overrides={k: D(v) for k, v in overrides.items()},
        adults=adults,
    )
    response = SimulationService.calculate_with_snapshot(bootstrap_snapshot(), request)
    return response.fiscal.net_public_impact.central


_reductions = st.dictionaries(
    st.sampled_from(CODES),
    st.integers(0, 100).map(str),
    max_size=len(CODES),
)
_overrides = st.dictionaries(
    st.sampled_from(CODES),
    st.integers(0, 20_000).map(str),
    max_size=4,
)
_adults = st.integers(1, 4)


# --------------------------------------------------------------------------- #
# Valeurs observées (3.1, 3.2, 3.3)                                             #
# --------------------------------------------------------------------------- #
def test_default_state_contribution_is_605_91() -> None:
    data = _post(_body())
    assert _total(data) == D("605.91")
    assert data["consommation"]["total_fr"].replace("\u202f", " ").replace("\xa0", " ").startswith(
        "605,91"
    )
    parts = data["consommation"]
    assert (D(parts["tva"]), D(parts["accises_carburant"]), D(parts["tva_carburant"])) == (
        D("523.60"),
        D("55.39"),
        D("26.92"),
    )


def test_explicit_zero_reductions_equal_default() -> None:
    assert _post(_body(reductions={c: "0" for c in CODES})) == _post(_body())


def test_all_amounts_zero_gives_zero_contribution() -> None:
    data = _post(_body(overrides={c: "0" for c in CODES}))
    assert _total(data) == 0
    parts = data["consommation"]
    assert D(parts["tva"]) == D(parts["accises_carburant"]) == D(parts["tva_carburant"]) == 0


def test_all_reductions_100_gives_zero_contribution() -> None:
    data = _post(_body(reductions={c: "100" for c in CODES}))
    assert _total(data) == 0
    parts = data["consommation"]
    assert D(parts["tva"]) == D(parts["accises_carburant"]) == D(parts["tva_carburant"]) == 0


# --------------------------------------------------------------------------- #
# Propriétés sur entrées variables (3.3, 3.5)                                   #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(reductions=_reductions, overrides=_overrides, adults=_adults)
def test_total_is_sum_of_parts_and_bounded_by_no_reduction(
    reductions: dict[str, str], overrides: dict[str, str], adults: int
) -> None:
    data = _post(_body(reductions, overrides, adults))
    parts = data["consommation"]
    assert _total(data) == D(parts["tva"]) + D(parts["accises_carburant"]) + D(
        parts["tva_carburant"]
    )
    assert _total(data) >= 0
    no_reduction = _post(_body({}, overrides, adults))
    assert _total(data) <= _total(no_reduction)


@_SETTINGS
@given(overrides=_overrides, adults=_adults)
def test_full_reduction_is_zero_whatever_overrides_and_adults(
    overrides: dict[str, str], adults: int
) -> None:
    data = _post(_body({c: "100" for c in CODES}, overrides, adults))
    assert _total(data) == 0


@_SETTINGS
@given(reductions=_reductions, overrides=_overrides, adults=_adults)
def test_bilan_includes_contribution_exactly(
    reductions: dict[str, str], overrides: dict[str, str], adults: int
) -> None:
    """Bilan et contributions ne varient que par la contribution de consommation."""
    data = _post(_body(reductions, overrides, adults))
    # Même ménage (adultes) sans réduction ni surcharge : seule la consommation change.
    reference = _post(_body(None, None, adults))
    delta = _total(data) - _total(reference)

    assert D(data["total_contributions"]) - D(reference["total_contributions"]) == delta
    assert D(data["bilan"]) - D(reference["bilan"]) == delta
    assert D(data["total_aides"]) == D(reference["total_aides"])
    assert D(data["impot_revenu"]) == D(reference["impot_revenu"])
    assert D(data["charges_salariales"]) == D(reference["charges_salariales"])
    assert D(data["montant_mis_en_avant"]) == abs(D(data["bilan"]))
    # Le verdict ne dépend que du signe du bilan : même signe => même verdict.
    if (D(data["bilan"]) > 0) == (D(reference["bilan"]) > 0) and D(data["bilan"]) != 0:
        assert data["verdict"] == reference["verdict"]


@_SETTINGS
@given(reductions=_reductions, overrides=_overrides, adults=st.integers(1, 3))
def test_contribution_does_not_decrease_with_more_adults(
    reductions: dict[str, str], overrides: dict[str, str], adults: int
) -> None:
    fewer = _post(_body(reductions, overrides, adults))
    more = _post(_body(reductions, overrides, adults + 1))
    assert _total(more) >= _total(fewer)


@_SETTINGS
@given(reductions=_reductions, overrides=_overrides, adults=_adults)
def test_response_is_deterministic(
    reductions: dict[str, str], overrides: dict[str, str], adults: int
) -> None:
    body = _body(reductions, overrides, adults)
    assert _post(body) == _post(body)


# --------------------------------------------------------------------------- #
# Impact sur les finances publiques inchangé et distinct (3.4)                  #
# --------------------------------------------------------------------------- #
def test_public_impact_is_zero_without_reduction() -> None:
    assert _public_impact({}, {}, 1) == 0
    assert _public_impact({c: "0" for c in CODES}, {}, 1) == 0


@_SETTINGS
@given(reductions=_reductions, overrides=_overrides, adults=_adults)
def test_public_impact_is_independent_of_consumption_contribution(
    reductions: dict[str, str], overrides: dict[str, str], adults: int
) -> None:
    impact = _public_impact(reductions, overrides, adults)
    assert impact == _public_impact(reductions, overrides, adults)
    if all(D(v) == 0 for v in reductions.values()):
        assert impact == 0
        # Contribution non nulle (sauf montants tous nuls) alors que l'impact reste 0.
        data = _post(_body(reductions, overrides, adults))
        assert _total(data) >= 0


# --------------------------------------------------------------------------- #
# Montant vide ou invalide : mêmes erreurs de champ (3.6)                       #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(
    code=st.sampled_from(CODES),
    bad=st.sampled_from(["", "abc", "-5", "12.345", "1e3", "10000001"]),
)
def test_invalid_amount_returns_same_field_error(code: str, bad: str) -> None:
    response = CLIENT.post(URL, json=_body(overrides={code: bad}))

    assert response.status_code == 400
    fields = response.json()["error"]["fields"]
    assert [f["field"] for f in fields] == [f"consommation.reference_overrides.{code}"]
    assert fields[0]["message"].startswith("Dépense de référence :")
    assert bad == "" or bad not in fields[0]["message"]


def test_invalid_reduction_returns_field_error() -> None:
    code = CODES[0]
    response = CLIENT.post(URL, json=_body(reductions={code: "101"}))
    assert response.status_code == 400
    fields = response.json()["error"]["fields"]
    assert [f["field"] for f in fields] == [f"consommation.reductions.{code}"]
