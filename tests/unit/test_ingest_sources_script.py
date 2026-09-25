"""Tests unitaires du script d'ingestion en lot (Exigences 11.1, 23.2 ; tâche 6.7).

Ces tests couvrent la **logique pure** d'analyse d'entrées de
``scripts/ingest_sources.py`` (``parse_entry``, ``read_entries_from_file``,
``collect_entries``) et l'émission des tâches via un ``ingest_document.delay``
factice injecté par monkeypatch — sans Celery ni broker Redis réels.
"""

from __future__ import annotations

import argparse

import pytest

from scripts.ingest_sources import (
    IngestEntry,
    collect_entries,
    dispatch_entries,
    parse_entry,
    read_entries_from_file,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# parse_entry                                                                  #
# --------------------------------------------------------------------------- #
def test_parse_entry_uses_default_source_id_for_bare_url() -> None:
    """Une URL nue utilise le ``default_source_id`` fourni."""
    entry = parse_entry("https://exemple.fr/a.pdf", default_source_id=5)
    assert entry == IngestEntry(url="https://exemple.fr/a.pdf", source_id=5)


def test_parse_entry_reads_inline_source_id() -> None:
    """La forme ``url,source_id`` surcharge le défaut."""
    entry = parse_entry("https://exemple.fr/a.pdf,7", default_source_id=5)
    assert entry == IngestEntry(url="https://exemple.fr/a.pdf", source_id=7)


def test_parse_entry_bare_url_without_default_raises() -> None:
    """Une URL nue sans défaut lève une ``ValueError`` explicite."""
    with pytest.raises(ValueError, match="Aucun source_id"):
        parse_entry("https://exemple.fr/a.pdf", default_source_id=None)


def test_parse_entry_rejects_non_integer_source_id() -> None:
    """Un ``source_id`` non entier lève une ``ValueError``."""
    with pytest.raises(ValueError, match="source_id invalide"):
        parse_entry("https://exemple.fr/a.pdf,abc", default_source_id=None)


def test_parse_entry_rejects_non_positive_source_id() -> None:
    """Un ``source_id`` ≤ 0 lève une ``ValueError``."""
    with pytest.raises(ValueError, match="strictement positif"):
        parse_entry("https://exemple.fr/a.pdf,0", default_source_id=None)


# --------------------------------------------------------------------------- #
# read_entries_from_file                                                       #
# --------------------------------------------------------------------------- #
def test_read_entries_from_file_ignores_blank_and_comment_lines(tmp_path) -> None:
    """Les lignes vides et commentaires (``#``) sont ignorées."""
    path = tmp_path / "sources.txt"
    path.write_text(
        "# commentaire\n"
        "https://exemple.fr/a.pdf,3\n"
        "\n"
        "https://exemple.fr/b.html,4\n",
        encoding="utf-8",
    )

    entries = read_entries_from_file(str(path), default_source_id=None)

    assert entries == [
        IngestEntry(url="https://exemple.fr/a.pdf", source_id=3),
        IngestEntry(url="https://exemple.fr/b.html", source_id=4),
    ]


# --------------------------------------------------------------------------- #
# collect_entries                                                              #
# --------------------------------------------------------------------------- #
def test_collect_entries_merges_file_and_cli() -> None:
    """Les entrées du fichier et des arguments sont rassemblées (défaut appliqué)."""
    args = argparse.Namespace(
        urls=["https://exemple.fr/c.txt"], source_id=9, file=None
    )
    entries = collect_entries(args)
    assert entries == [IngestEntry(url="https://exemple.fr/c.txt", source_id=9)]


# --------------------------------------------------------------------------- #
# dispatch_entries — émission de la tâche Celery (mockée)                      #
# --------------------------------------------------------------------------- #
def test_dispatch_entries_emits_one_task_per_entry(monkeypatch) -> None:
    """Chaque entrée émet exactement un ``ingest_document.delay(url, source_id)``."""
    calls: list[tuple[str, int]] = []

    class _FakeResult:
        id = "task-xyz"

    class _FakeTask:
        @staticmethod
        def delay(url: str, source_id: int) -> _FakeResult:
            calls.append((url, source_id))
            return _FakeResult()

    # Le module importe ``ingest_document`` de façon différée dans la fonction ;
    # on remplace l'attribut dans le module worker source de l'import.
    import app.workers.ingestion as ingestion_module

    monkeypatch.setattr(ingestion_module, "ingest_document", _FakeTask)

    entries = [
        IngestEntry(url="https://exemple.fr/a.pdf", source_id=3),
        IngestEntry(url="https://exemple.fr/b.html", source_id=4),
    ]
    results = dispatch_entries(entries)

    assert calls == [
        ("https://exemple.fr/a.pdf", 3),
        ("https://exemple.fr/b.html", 4),
    ]
    assert [task_id for _, task_id in results] == ["task-xyz", "task-xyz"]
