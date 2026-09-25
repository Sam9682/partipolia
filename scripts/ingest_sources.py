"""Script d'ingestion documentaire en lot par URL (Exigences 11.1, 23.2, 23.3).

Émet, pour chaque URL fournie, la tâche Celery ``ingest_document`` (via
``.delay(...)``) afin d'alimenter la base documentaire utilisée par
l'Assistant_IA. Comme l'endpoint ``POST /api/v1/admin/documents/ingest``, le
script ne fait qu'**émettre** les tâches : le travail long (téléchargement,
extraction, chunking, embeddings, écriture pgvector) se déroule dans le
Worker_Celery, hors du chemin d'appel (Exigence 23.3).

Chaque entrée associe une ``url`` à un ``source_id`` (la Source de rattachement,
créée au préalable via ``POST /api/v1/sources``). Les URL peuvent être passées
en ligne de commande ou lues depuis un fichier.

Utilisation :

    # Une ou plusieurs paires url=source_id en argument :
    uv run python -m scripts.ingest_sources \\
        --source-id 3 https://exemple.fr/a.pdf https://exemple.fr/b.html

    # Paires explicites url,source_id (une par argument) :
    uv run python -m scripts.ingest_sources \\
        https://exemple.fr/a.pdf,3 https://exemple.fr/b.html,5

    # Depuis un fichier (une entrée « url,source_id » par ligne ; # = commentaire) :
    uv run python -m scripts.ingest_sources --file sources.txt

Le ``--source-id`` global s'applique aux URL nues (sans ``,source_id``). Chaque
entrée du fichier ou de la ligne de commande peut surcharger ce défaut avec la
forme ``url,source_id``.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class IngestEntry:
    """Une entrée d'ingestion : l'``url`` du Document et sa Source (``source_id``)."""

    url: str
    source_id: int


def parse_entry(token: str, *, default_source_id: int | None) -> IngestEntry:
    """Analyse un jeton ``url`` ou ``url,source_id`` en :class:`IngestEntry`.

    Un jeton nu (sans ``,source_id``) utilise ``default_source_id`` ; en son
    absence, une :class:`ValueError` explicite est levée. Le ``source_id`` doit
    être un entier strictement positif.
    """
    token = token.strip()
    if not token:
        raise ValueError("Entrée vide.")

    if "," in token:
        url_part, _, source_part = token.rpartition(",")
        url = url_part.strip()
        source_raw = source_part.strip()
        if not url:
            raise ValueError(f"URL manquante dans l'entrée : {token!r}.")
        try:
            source_id = int(source_raw)
        except ValueError as exc:
            raise ValueError(
                f"source_id invalide dans l'entrée {token!r} : {source_raw!r}."
            ) from exc
    else:
        url = token
        if default_source_id is None:
            raise ValueError(
                f"Aucun source_id pour {url!r} : utilisez « url,source_id » "
                "ou l'option --source-id."
            )
        source_id = default_source_id

    if source_id <= 0:
        raise ValueError(f"source_id doit être strictement positif : {source_id}.")
    return IngestEntry(url=url, source_id=source_id)


def read_entries_from_file(path: str, *, default_source_id: int | None) -> list[IngestEntry]:
    """Lit les entrées d'un fichier (une par ligne ; lignes vides et ``#`` ignorées)."""
    entries: list[IngestEntry] = []
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            entries.append(parse_entry(line, default_source_id=default_source_id))
    return entries


def collect_entries(args: argparse.Namespace) -> list[IngestEntry]:
    """Rassemble les entrées issues du fichier et/ou des arguments de ligne."""
    entries: list[IngestEntry] = []
    if args.file:
        entries.extend(
            read_entries_from_file(args.file, default_source_id=args.source_id)
        )
    for token in args.urls:
        entries.append(parse_entry(token, default_source_id=args.source_id))
    return entries


def dispatch_entries(entries: list[IngestEntry]) -> list[tuple[IngestEntry, str | None]]:
    """Émet la tâche Celery ``ingest_document`` pour chaque entrée (Exigence 23.3).

    L'import de la tâche est différé pour découpler le module de Celery lors des
    tests unitaires de l'analyse d'arguments. Retourne, pour chaque entrée, le
    couple ``(entrée, task_id)`` (``task_id`` peut être ``None``).
    """
    from app.workers.ingestion import ingest_document

    results: list[tuple[IngestEntry, str | None]] = []
    for entry in entries:
        async_result = ingest_document.delay(entry.url, entry.source_id)
        results.append((entry, getattr(async_result, "id", None)))
    return results


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Analyse les arguments CLI du script d'ingestion en lot."""
    parser = argparse.ArgumentParser(
        prog="ingest_sources",
        description=(
            "Émet en lot les tâches Celery d'ingestion documentaire par URL "
            "(Exigences 11.1, 23.2, 23.3)."
        ),
    )
    parser.add_argument(
        "urls",
        nargs="*",
        help="URL à ingérer (« url » avec --source-id, ou « url,source_id »).",
    )
    parser.add_argument(
        "--source-id",
        type=int,
        default=None,
        help="source_id par défaut appliqué aux URL nues (sans « ,source_id »).",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Fichier d'entrées (une « url,source_id » par ligne ; # = commentaire).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Point d'entrée synchrone pour ``python -m scripts.ingest_sources``."""
    args = _parse_args(argv)

    try:
        entries = collect_entries(args)
    except (ValueError, OSError) as exc:
        raise SystemExit(f"Erreur : {exc}")

    if not entries:
        raise SystemExit(
            "Aucune URL à ingérer : fournissez des URL en argument ou --file."
        )

    results = dispatch_entries(entries)
    for entry, task_id in results:
        suffix = f" (tâche {task_id})" if task_id else ""
        print(f"Ingestion émise : {entry.url} → source {entry.source_id}{suffix}.")
    print(f"{len(results)} tâche(s) d'ingestion émise(s).")


if __name__ == "__main__":
    main()
