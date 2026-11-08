"""Tests de préservation (observation-first) : « Mes revenus » sur /participer.

Spec bugfix : ``.kiro/specs/participer-revenus-not-loaded-from-db`` (tâche 2).

**Property 2: Preservation** : comportement hors condition de bug inchangé.

Ces tests sont écrits sur le code NON corrigé et DOIVENT passer avant comme après
le correctif (tâche 3.7). Ils capturent le comportement observé quand
``isBugCondition`` est faux :

* la réponse de ``calculate`` est un calcul pur côté serveur, identique pour un
  anonyme et un utilisateur connecté (Exigences 3.2, 3.5) ;
* un anonyme n'écrit rien en base (Exigences 3.1, 3.2) ;
* un payload invalide renvoie des erreurs 400 par champ sans aucune écriture
  (Exigence 3.4) ;
* un utilisateur connecté qui réussit un calcul voit son verdict et son solde
  persistés (Exigence 3.5) ;
* le partial n'utilise aucun stockage navigateur et démarre vide (Exigences 3.1,
  3.2, 3.3) ;
* l'export existant conserve ses champs (Exigence 3.7).

Sans base de données : la session est une doublure qui enregistre les
instructions SQL exécutées, ce qui permet d'observer les écritures.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8**
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from sqlalchemy.dialects import postgresql

from app.api.deps import get_current_user_optional
from app.api.v1 import net_balance
from app.core.database import get_session
from app.schemas.auth import UserPublic
from app.schemas.privacy import DataExport
from app.services.net_balance_service import NetBalanceService
from app.services.simulation_service import BootstrapSnapshotProvider
from app.web import router as web

pytestmark = pytest.mark.api

_NOW = datetime(2024, 1, 1, tzinfo=timezone.utc)
_STATUTS = ["salarie", "sans_emploi", "fonctionnaire", "entrepreneur"]
_VERDICT_TABLE = "user_net_balance_verdicts"
_PARTIAL = web.TEMPLATES_DIR / "public" / "partials" / "net_balance.html"

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --------------------------------------------------------------------------- #
# Doublures                                                                    #
# --------------------------------------------------------------------------- #
class _Nested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _RecordingSession:
    """Session factice : enregistre le SQL exécuté, ne touche aucune base."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def begin_nested(self) -> _Nested:
        return _Nested()

    async def execute(self, stmt: Any, *args: Any, **kwargs: Any) -> Any:
        self.statements.append(
            str(stmt.compile(dialect=postgresql.dialect())).lower()
        )
        return None

    def add(self, *args: Any, **kwargs: Any) -> None:
        self.statements.append("add")

    async def flush(self, *args: Any, **kwargs: Any) -> None:
        return None

    async def commit(self) -> None:
        return None


def _user() -> UserPublic:
    return UserPublic(
        id=7,
        email="citoyen@example.org",
        display_name="Citoyen",
        is_active=True,
        is_verified=True,
        is_admin=False,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _client(*, authenticated: bool) -> tuple[TestClient, _RecordingSession]:
    session = _RecordingSession()
    app = FastAPI()
    app.include_router(net_balance.router, prefix="/api/v1/net-balance")
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[net_balance.get_net_balance_service] = lambda: (
        NetBalanceService(BootstrapSnapshotProvider())
    )
    app.dependency_overrides[get_current_user_optional] = (
        _user if authenticated else (lambda: None)
    )
    return TestClient(app), session


def _writes(session: _RecordingSession) -> list[str]:
    return [
        s
        for s in session.statements
        if s == "add" or s.startswith(("insert", "update", "delete"))
    ]


# --------------------------------------------------------------------------- #
# Stratégies                                                                   #
# --------------------------------------------------------------------------- #
_valid_payloads = st.fixed_dictionaries(
    {
        "revenus": st.lists(
            st.fixed_dictionaries(
                {
                    "statut": st.sampled_from(_STATUTS),
                    "revenu_net": st.integers(0, 200_000).map(str),
                }
            ),
            min_size=0,
            max_size=1,
        ),
        "enfants_a_charge": st.integers(0, 4).map(str),
    }
)

_invalid_payloads = st.one_of(
    st.builds(
        lambda s, v: {"revenus": [{"statut": s, "revenu_net": v}]},
        st.sampled_from(_STATUTS),
        st.integers(-200_000, -1).map(str),
    ),
    st.builds(
        lambda v: {"revenus": [{"statut": "statut_inconnu", "revenu_net": str(v)}]},
        st.integers(0, 100_000),
    ),
    st.builds(
        lambda s: {"revenus": [{"statut": s, "revenu_net": "abc"}]},
        st.sampled_from(_STATUTS),
    ),
)


# --------------------------------------------------------------------------- #
# Calcul pur : réponse identique anonyme / connecté (3.2, 3.5)                  #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(payload=_valid_payloads)
def test_calculate_response_is_identical_for_anonymous_and_logged_in(
    payload: dict[str, Any],
) -> None:
    anon_client, _ = _client(authenticated=False)
    auth_client, _ = _client(authenticated=True)

    anon = anon_client.post("/api/v1/net-balance/calculate", json=payload)
    auth = auth_client.post("/api/v1/net-balance/calculate", json=payload)

    assert anon.status_code == 200
    assert auth.status_code == 200
    assert anon.json() == auth.json()
    body = anon.json()
    assert {"verdict", "bilan", "impot_revenu", "tranche_marginale", "taux_moyen_effectif"} <= set(
        body
    )


# --------------------------------------------------------------------------- #
# Anonyme : aucune écriture (3.1, 3.2)                                          #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(payload=st.one_of(_valid_payloads, _invalid_payloads))
def test_anonymous_never_writes_to_database(payload: dict[str, Any]) -> None:
    client, session = _client(authenticated=False)

    client.post("/api/v1/net-balance/calculate", json=payload)

    assert session.statements == []


# --------------------------------------------------------------------------- #
# Invalide : erreurs 400 par champ, aucune écriture (3.4)                       #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(payload=_invalid_payloads)
def test_invalid_payload_returns_field_errors_and_writes_nothing(
    payload: dict[str, Any],
) -> None:
    client, session = _client(authenticated=True)

    response = client.post("/api/v1/net-balance/calculate", json=payload)

    assert response.status_code == 400
    fields = response.json()["error"]["fields"]
    assert fields
    assert all(f["field"].startswith("revenus") for f in fields)
    assert _writes(session) == []


# --------------------------------------------------------------------------- #
# Connecté + calcul valide : verdict et solde persistés (3.5)                   #
# --------------------------------------------------------------------------- #
@_SETTINGS
@given(payload=_valid_payloads)
def test_logged_in_valid_calculation_persists_verdict(payload: dict[str, Any]) -> None:
    client, session = _client(authenticated=True)

    response = client.post("/api/v1/net-balance/calculate", json=payload)

    assert response.status_code == 200
    verdict_writes = [
        s for s in session.statements if s.startswith("insert") and _VERDICT_TABLE in s
    ]
    assert len(verdict_writes) == 1
    assert "on conflict" in verdict_writes[0]


# --------------------------------------------------------------------------- #
# Partial : aucun stockage navigateur, formulaire initial vide (3.1-3.3)        #
# --------------------------------------------------------------------------- #
def _partial_code() -> str:
    """Contenu du partial sans les commentaires qui mentionnent le stockage."""
    text = _PARTIAL.read_text(encoding="utf-8")
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"//[^\n]*", "", text)
    return text


@pytest.mark.parametrize("token", ["localStorage", "sessionStorage", "document.cookie"])
def test_partial_uses_no_browser_storage(token: str) -> None:
    assert token not in _partial_code()


def test_partial_form_starts_empty() -> None:
    text = _PARTIAL.read_text(encoding="utf-8")
    assert re.search(r"form:\s*\{\s*statut:\s*null,\s*revenu:\s*''", text)


# --------------------------------------------------------------------------- #
# Export existant : champs actuels conservés (3.7)                              #
# --------------------------------------------------------------------------- #
def test_data_export_keeps_existing_fields() -> None:
    existing = {
        "generated_at",
        "account",
        "proposals",
        "votes",
        "arguments",
        "comments",
        "saved_scenarios",
        "claims",
        "claim_comments",
        "consultation_participations",
    }
    assert existing <= set(DataExport.model_fields)
