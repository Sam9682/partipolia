"""Tâches Celery (ingestion, embeddings, modération, statistiques) — Exigence 23.

Ce paquet regroupe l'application Celery partagée (:mod:`app.workers.celery_app`)
et les tâches asynchrones exécutées par le Worker Celery, hors du chemin de
requête FastAPI (Exigence 23.3).

Tâches d'ingestion documentaire (:mod:`app.workers.ingestion`) :

* ``ingest_document`` — orchestration complète : extraction → chunking →
  embeddings → écriture dans ``document_chunks`` (VECTOR + HNSW). Idempotente par
  checksum (Exigence 11.3).
* ``reindex_document`` — reconstruction des chunks/embeddings d'un Document.
* ``extract_document`` / ``chunk_document`` / ``generate_embeddings`` —
  sous-étapes dédiées (Exigence 23.1).

L'orchestration métier pure vit dans :class:`~app.workers.ingestion.DocumentIngestor`,
testable hors-ligne sans broker ni base réelle.
"""

from __future__ import annotations

__all__ = [
    "celery_app",
    "ingest_document",
    "reindex_document",
    "extract_document",
    "chunk_document",
    "generate_embeddings",
]


def __getattr__(name: str) -> object:
    """Exposition paresseuse des tâches/app pour éviter d'importer Celery à vide.

    L'import différé garde ``app.workers`` importable sans effet de bord lourd et
    ne charge l'application Celery que lorsqu'un symbole est réellement demandé.
    """
    if name == "celery_app":
        from app.workers.celery_app import celery_app

        return celery_app
    if name in {
        "ingest_document",
        "reindex_document",
        "extract_document",
        "chunk_document",
        "generate_embeddings",
    }:
        from app.workers import ingestion

        return getattr(ingestion, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
