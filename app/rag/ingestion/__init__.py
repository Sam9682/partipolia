"""Modules d'ingestion documentaire (`app/rag/ingestion/`) — Exigence 11.

Ce paquet regroupe les briques *métier pures* du pipeline d'ingestion, dans
l'ordre du flux (cf. design.md « Flux du Pipeline d'ingestion documentaire ») :

``URL → téléchargement → détection de format → extraction → nettoyage →
déduplication (checksum) → chunking``

Chaque brique est une classe sans état, à responsabilité unique, orchestrée
hors-ligne par les tâches Celery (tâche 6.6) et couverte par les tests de
propriété 6.4/6.5 (Property 5 : idempotence par checksum ; Property 6 :
invariants de chunking et préservation des métadonnées).

Composants exposés :

* :class:`RawDocument` — contenu binaire brut + en-têtes/URL récupérés.
* :class:`DocFormat` — format détecté (``HTML`` / ``PDF`` / ``TXT`` / ``CSV``).
* :class:`Chunk` — fragment de texte + métadonnées complètes.
* :class:`Downloader` — téléchargement d'une URL → :class:`RawDocument`.
* :class:`FormatDetector` — détection de format → :class:`DocFormat`.
* :class:`TextExtractor` — extraction de texte selon le format.
* :class:`TextCleaner` — nettoyage/normalisation du texte extrait.
* :class:`Deduplicator` — checksum déterministe + test « déjà connu ».
* :class:`Chunker` — découpage ~800 tokens / chevauchement 120 tokens.

Ces modules restent isolés de la couche domaine et des Providers IA : ils ne
manipulent que des types simples et n'écrivent pas en base (l'orchestration
Celery s'en charge).
"""

from __future__ import annotations

from app.rag.ingestion.chunker import Chunk, Chunker, ChunkMetadata
from app.rag.ingestion.cleaner import TextCleaner
from app.rag.ingestion.dedup import Deduplicator
from app.rag.ingestion.downloader import Downloader
from app.rag.ingestion.extractor import TextExtractor
from app.rag.ingestion.format_detector import DocFormat, FormatDetector
from app.rag.ingestion.types import RawDocument

__all__ = [
    "RawDocument",
    "DocFormat",
    "Chunk",
    "ChunkMetadata",
    "Downloader",
    "FormatDetector",
    "TextExtractor",
    "TextCleaner",
    "Deduplicator",
    "Chunker",
]
