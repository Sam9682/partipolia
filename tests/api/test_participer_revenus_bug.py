"""Test d'exploration de la condition de bug — « Mes revenus » non lus en base.

Spec bugfix : ``.kiro/specs/participer-revenus-not-loaded-from-db`` (tâche 1).

**Property 1: Bug Condition** — Pour un utilisateur connecté, le statut et le
revenu net saisis dans « Mes revenus » ne sont ni écrits ni relus depuis
PostgreSQL.

**CRITIQUE** : ce test DOIT ÉCHOUER sur le code NON corrigé — l'échec confirme
que le bug existe. Il encode aussi le comportement attendu : il PASSERA une fois
le correctif appliqué (tâche 3.6), sans être réécrit.

On encode ``isBugCondition`` de design.md (utilisateur authentifié +
``VALID_CALCULATION`` avec statut et revenu net, ou ``PAGE_LOAD`` avec revenus
stockés) :

* **Cas 1** — après ``POST /api/v1/net-balance/calculate`` valide (connecté), une
  écriture ``user_incomes`` portant exactement le statut et le revenu net saisis
  est émise (échoue : seul le verdict est persisté).
* **Cas 2** — ``GET /api/v1/net-balance/incomes/me`` restitue ces valeurs
  (échoue : ``404``, route absente), et le calcul relancé avec elles renvoie
  l'impôt calculé par le serveur (montant, tranche marginale, taux moyen).
* **Cas 3** — le partial ``net_balance.html`` appelle ``/incomes/me`` dans
  ``init()`` (échoue : ``init()`` ne fait aucun appel de lecture).
* **Cas 4** — ``DataExport`` expose un champ ``income`` (échoue : absent).

Aucune base PostgreSQL n'est démarrée : la session SQLAlchemy est remplacée par
une doublure en mémoire qui capture les instructions SQL exécutées et rejoue
les valeurs écrites dans ``user_incomes`` pour la lecture. Le calcul utilise
``BootstrapSnapshotProvider`` (pas de lecture BD). Aucun code applicatif n'est
modifié par cette tâche.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.api.deps import get_current_user, get_current_user_optional
from app.api.v1 import net_balance
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.privacy import DataExport
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)

_INCOMES_TABLE = "user_incomes"
_INCOMES_ME_URL = "/api/v1/net-balance/incomes/me"
_CALCULATE_URL = "/api/v1/net-balance/calculate"

# Statuts connus de ``net_balance_service`` (cf. partial net_balance.html).
_STATUTS = ["salarie", "sans_emploi", "fonctionnaire", "entrepreneur"]

_PARTIAL = (
    Path(net_balance.__file__).resolve().parents[2]
    / "templates"
    / "public"
    / "partials"
    / "net_balance.html"
)


def _user() -> UserPublic:
    return UserPublic(
        id=42,
        email="alice@example.org",
        display_name="Alice",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


# --------------------------------------------------------------------------- #
# Session factice : capture les instructions SQL, rejoue user_incomes.         #
# --------------------------------------------------------------------------- #
class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def scalar_one_or_none(self) -> object | None:
        return self._row

    def scalars(self) -> "_Result":
        return self

    def first(self) -> object | None:
        return self._row

    def all(self) -> list[object]:
        return [] if self._row is None else [self._row]


class _FakeSession:
    """``AsyncSession`` minimale : journal des instructions et table de revenus."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.income_writes: list[dict[str, Any]] = []
        self._income_row: object | None = None

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> _Result:
        sql = str(stmt)
        self.statements.append(sql)
        if _INCOMES_TABLE in sql:
            if sql.lstrip().upper().startswith("INSERT"):
                params = dict(stmt.compile().params)
                self.income_writes.append(params)
                self._income_row = SimpleNamespace(
                    user_id=params.get("user_id"),
                    statut=params.get("statut"),
                    revenu_net=params.get("revenu_net"),
                    updated_at=_NOW,
                )
            else:
                return _Result(self._income_row)
        return _Result(None)

    async def add(self, *a: Any, **k: Any) -> None:  # pragma: no cover
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _build_app(session: _FakeSession) -> FastAPI:
    app = FastAPI()
    app.include_router(net_balance.router, prefix="/api/v1/net-balance")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[net_balance.get_net_balance_service] = (
        lambda: NetBalanceService(BootstrapSnapshotProvider())
    )
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_current_user_optional] = _user
    # Neutralise la limitation de débit (Redis absent en test) sur chaque route.
    for route in app.routes:
        for dep in getattr(route, "dependencies", []):
            app.dependency_overrides[dep.dependency] = lambda: None
    return app


def _payload(statut: str, revenu: int) -> dict[str, Any]:
    return {"revenus": [{"statut": statut, "revenu_net": str(revenu)}]}


def _as_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# Cas 1 + 2 — écriture après calcul valide, lecture, impôt recalculé.          #
# --------------------------------------------------------------------------- #
@settings(max_examples=15, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    statut=st.sampled_from(_STATUTS),
    revenu=st.integers(min_value=1_000, max_value=150_000),
)
def test_valid_calculation_stores_and_restores_incomes(statut: str, revenu: int) -> None:
    """Calcul valide connecté → ligne ``user_incomes`` exacte, relue par ``/incomes/me``.

    Property (Bug Condition) : sur le code NON corrigé, ce test ÉCHOUE — aucune
    écriture ``user_incomes`` n'est émise et ``GET /incomes/me`` renvoie 404.

    _Requirements: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 2.4_
    """
    session = _FakeSession()
    client = TestClient(_build_app(session))

    response = client.post(_CALCULATE_URL, json=_payload(statut, revenu))
    assert response.status_code == 200, response.text

    # 2.4 — écriture en base du statut et du revenu net saisis.
    assert session.income_writes, (
        f"calculate({statut!r}, {revenu}) connecté n'a émis aucune écriture "
        f"{_INCOMES_TABLE!r} (instructions vues : {session.statements!r})."
    )
    write = session.income_writes[-1]
    assert write.get("statut") == statut
    assert _as_decimal(write.get("revenu_net")) == Decimal(revenu)

    # 2.1 / 2.2 — lecture : statut et revenu net restitués au chargement.
    stored = client.get(_INCOMES_ME_URL)
    assert stored.status_code == 200, (
        f"GET {_INCOMES_ME_URL} : statut {stored.status_code} (attendu 200)."
    )
    body = stored.json()
    assert body["statut"] == statut
    assert _as_decimal(body["revenu_net"]) == Decimal(revenu)

    # 2.3 — le calcul relancé avec les valeurs lues renvoie l'impôt serveur.
    again = client.post(
        _CALCULATE_URL, json=_payload(body["statut"], int(Decimal(body["revenu_net"])))
    )
    assert again.status_code == 200
    data = again.json()
    for key in ("impot_revenu", "tranche_marginale", "taux_moyen_effectif"):
        assert key in data and data[key] is not None


# --------------------------------------------------------------------------- #
# Cas 3 — le partial lit /incomes/me dans init().                              #
# --------------------------------------------------------------------------- #
def test_partial_init_reads_incomes_endpoint() -> None:
    """``init()`` du partial ``netBalance()`` appelle ``/api/v1/net-balance/incomes/me``.

    Sur le code NON corrigé, ce test ÉCHOUE — ``init()`` ne fait aucun appel de
    lecture (le formulaire reste vide).

    _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_
    """
    source = _PARTIAL.read_text(encoding="utf-8")
    match = re.search(r"\n      init\(\) \{(.*?)\n      \},?\n", source, re.DOTALL)
    assert match, "init() introuvable dans net_balance.html."
    assert "/api/v1/net-balance/incomes/me" in match.group(1), (
        "init() de netBalance() n'appelle pas /api/v1/net-balance/incomes/me : "
        "les revenus enregistrés ne sont pas pré-remplis."
    )


# --------------------------------------------------------------------------- #
# Cas 4 — l'export des données contient les revenus.                           #
# --------------------------------------------------------------------------- #
def test_data_export_exposes_income_field() -> None:
    """``DataExport`` expose un champ ``income`` (statut, revenu net).

    Sur le code NON corrigé, ce test ÉCHOUE — le champ n'existe pas.

    _Requirements: 1.4, 2.4_
    """
    assert "income" in DataExport.model_fields, (
        "DataExport n'a pas de champ 'income' : l'export omet les revenus. "
        f"Champs : {sorted(DataExport.model_fields)!r}."
    )
