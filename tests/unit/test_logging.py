"""Tests unitaires de `app.core.logging` (journalisation structurée + request_id).

Couvre les Exigences 30.1 (journalisation structurée avec identifiant de requête,
réutilisable par API/Worker/base/RAG) et 26.3 (X-Request-ID + logs structurés).
"""

from __future__ import annotations

import json
import logging

import pytest

# Ces tests vérifient le **rendu structuré réel** (JSON, request_id) et exigent
# donc la vraie bibliothèque ``structlog``. Dans un environnement hors-ligne où
# seul le stub non intrusif du ``conftest`` racine est disponible, ils sont
# ignorés proprement (comme les tests exigeant PostgreSQL), plutôt que de produire
# de faux échecs. Le stub n'expose pas ``JSONRenderer`` en tant que classe réelle
# de rendu ; on détecte sa présence via un attribut propre à la vraie lib.
_structlog = pytest.importorskip("structlog")
if not hasattr(getattr(_structlog, "processors", object()), "KeyValueRenderer"):
    pytest.skip(
        "structlog réel indisponible (stub hors-ligne) : rendu structuré non testable.",
        allow_module_level=True,
    )

from app.core import logging as app_logging


@pytest.fixture(autouse=True)
def _reset_request_id() -> None:
    """Garantit un contexte propre avant chaque test."""
    assert app_logging.get_request_id() is None


def _read_json(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    """Retourne la dernière ligne JSON émise sur stdout."""
    out = capsys.readouterr().out.strip().splitlines()
    assert out, "aucune sortie de log capturée"
    return json.loads(out[-1])


def test_configure_logging_emits_json_with_expected_fields(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_logging.configure_logging(json_logs=True, level="INFO")
    log = app_logging.get_logger("test.json")

    log.info("event_test", foo="bar")

    record = _read_json(capsys)
    assert record["event"] == "event_test"
    assert record["foo"] == "bar"
    assert record["level"] == "info"
    assert "timestamp" in record


def test_request_id_injected_from_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_logging.configure_logging(json_logs=True)
    log = app_logging.get_logger("test.request_id")

    with app_logging.bind_request_id("req-123") as rid:
        assert rid == "req-123"
        log.info("with_request_id")

    record = _read_json(capsys)
    assert record["request_id"] == "req-123"


def test_request_id_absent_outside_context(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_logging.configure_logging(json_logs=True)
    log = app_logging.get_logger("test.no_request_id")

    log.info("no_request_id")

    record = _read_json(capsys)
    assert "request_id" not in record


def test_bind_request_id_restores_previous_value() -> None:
    assert app_logging.get_request_id() is None
    with app_logging.bind_request_id("outer"):
        assert app_logging.get_request_id() == "outer"
        with app_logging.bind_request_id("inner"):
            assert app_logging.get_request_id() == "inner"
        assert app_logging.get_request_id() == "outer"
    assert app_logging.get_request_id() is None


def test_bind_request_id_generates_when_absent() -> None:
    with app_logging.bind_request_id() as rid:
        assert rid
        assert app_logging.get_request_id() == rid


def test_set_and_clear_request_id_roundtrip() -> None:
    token = app_logging.set_request_id("manual")
    assert app_logging.get_request_id() == "manual"
    app_logging.clear_request_id(token)
    assert app_logging.get_request_id() is None


def test_generate_request_id_is_unique() -> None:
    ids = {app_logging.generate_request_id() for _ in range(100)}
    assert len(ids) == 100


def test_explicit_request_id_in_log_is_preserved(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_logging.configure_logging(json_logs=True)
    log = app_logging.get_logger("test.explicit")

    with app_logging.bind_request_id("ctx-id"):
        log.info("explicit", request_id="explicit-id")

    record = _read_json(capsys)
    assert record["request_id"] == "explicit-id"


def test_stdlib_logs_pass_through_structlog(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Les logs de la bibliothèque standard sont formatés en JSON + request_id."""
    app_logging.configure_logging(json_logs=True)
    std_logger = logging.getLogger("test.stdlib")

    with app_logging.bind_request_id("std-req"):
        std_logger.warning("stdlib message")

    record = _read_json(capsys)
    assert record["request_id"] == "std-req"
    assert record["level"] == "warning"


def test_get_logger_binds_initial_values(
    capsys: pytest.CaptureFixture[str],
) -> None:
    app_logging.configure_logging(json_logs=True)
    log = app_logging.get_logger("test.bound", component="rag")

    log.info("bound_event")

    record = _read_json(capsys)
    assert record["component"] == "rag"
