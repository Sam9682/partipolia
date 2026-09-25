"""Tâches Celery d'ingestion documentaire (`app/workers/`) — Exigences 11, 23.

Ce module câble les briques *métier pures* de :mod:`app.rag.ingestion`
(``Downloader`` → ``FormatDetector`` → ``TextExtractor`` → ``TextCleaner`` →
``Deduplicator`` → ``Chunker``) et les Providers d'embeddings
(:mod:`app.rag.providers`) au sein de tâches Celery orchestrant le flux complet
hors du chemin de requête FastAPI (Exigence 23.3).

Découpage :

* :class:`DocumentIngestor` — **logique d'orchestration pure**, sans Celery ni
  session concrète. Toutes ses dépendances (étapes du pipeline, provider
  d'embeddings, port de persistance) sont injectées, ce qui la rend testable
  hors-ligne sans broker ni base réelle.
* :class:`IngestionStore` — *port* de persistance (Protocol) que l'orchestrateur
  appelle pour lire/écrire ``documents`` et ``document_chunks``. Le port isole la
  logique de l'``AsyncSession`` SQLAlchemy.
* Les fonctions ``@celery_app.task`` (:func:`ingest_document`,
  :func:`extract_document`, :func:`chunk_document`, :func:`generate_embeddings`,
  :func:`reindex_document`) : fines enveloppes qui ouvrent une session, exécutent
  l'orchestrateur et appliquent le **réessai avec backoff exponentiel** aux
  erreurs transitoires (Exigence 23 — « Échecs des tâches Celery »).

Invariants clés :

* **Idempotence par checksum** (Exigence 11.3) : si le checksum du contenu
  nettoyé est déjà connu, l'ingestion est ignorée sans réécriture.
* **Embeddings stockés dans ``document_chunks``** (VECTOR + index HNSW,
  Exigence 11.6) : l'orchestrateur écrit un vecteur par chunk.
* **API non bloquée** (Exigence 23.3) : tout le travail long se déroule dans le
  worker ; l'API se contente d'émettre les tâches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.rag.ingestion import (
    Chunk,
    Chunker,
    ChunkMetadata,
    Deduplicator,
    Downloader,
    FormatDetector,
    TextCleaner,
    TextExtractor,
)
from app.rag.providers import EmbeddingProvider


class _NullLogger:
    """Logger de repli sans effet.

    Utilisé si la couche de journalisation structurée n'est pas disponible
    (environnement de test hors-ligne dépourvu de ``structlog``). Il absorbe tout
    appel ``.info(...)`` / ``.warning(...)`` etc. sans erreur, de sorte que la
    *logique* d'ingestion reste testable indépendamment de l'observabilité.
    """

    def __getattr__(self, _name: str) -> Any:
        def _noop(*_args: object, **_kwargs: object) -> None:
            return None

        return _noop


class _null_context:
    """Context manager sans effet (repli de ``bind_request_id``)."""

    def __enter__(self) -> str:
        return ""

    def __exit__(self, *_exc: object) -> bool:
        return False


def _log() -> Any:
    """Retourne le logger structuré (import différé de :mod:`app.core.logging`).

    L'import est différé — et son échec toléré — pour que l'orchestrateur pur
    reste exécutable dans un environnement dépourvu des dépendances de
    journalisation ; seule l'exécution réelle charge la couche logging complète.
    """
    try:
        from app.core.logging import get_logger
    except Exception:  # pragma: no cover - couche logging indisponible
        return _NullLogger()
    return get_logger(__name__)


def _bind_request_id() -> Any:
    """Context manager de corrélation (import différé, cf. :func:`_log`)."""
    try:
        from app.core.logging import bind_request_id
    except Exception:  # pragma: no cover - couche logging indisponible
        return _null_context()
    return bind_request_id()


# ---------------------------------------------------------------------------
# Résultat d'ingestion
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Compte rendu d'une ingestion (retour des tâches, sérialisable JSON).

    * ``status`` — ``"ingested"`` (nouveau Document indexé), ``"skipped"``
      (checksum déjà connu — idempotence) ou ``"reindexed"``.
    * ``document_id`` — identifiant du Document en base (``None`` si ignoré et
      qu'aucun Document préexistant n'a été trouvé).
    * ``chunk_count`` — nombre de Chunks_De_Document écrits (0 si ignoré).
    * ``checksum`` — checksum du contenu nettoyé.
    """

    status: str
    document_id: int | None
    chunk_count: int
    checksum: str

    def as_dict(self) -> dict[str, Any]:
        """Sérialise le résultat pour le back-end de résultats Celery (JSON)."""
        return {
            "status": self.status,
            "document_id": self.document_id,
            "chunk_count": self.chunk_count,
            "checksum": self.checksum,
        }


# ---------------------------------------------------------------------------
# Port de persistance
# ---------------------------------------------------------------------------
class IngestionStore(Protocol):
    """Port de persistance de l'ingestion (isole l'``AsyncSession``).

    L'orchestrateur ne connaît que ce contrat ; l'implémentation concrète
    (SQLAlchemy async) est fournie par la tâche Celery. En test, une
    implémentation en mémoire suffit.
    """

    async def find_document_id_by_checksum(self, checksum: str) -> int | None:
        """Retourne l'id du Document portant ce ``checksum``, ou ``None``."""
        ...

    async def create_document(
        self, *, source_id: int, checksum: str, meta: dict[str, Any] | None
    ) -> int:
        """Crée un ``documents`` et retourne son id (checksum UNIQUE)."""
        ...

    async def delete_chunks(self, document_id: int) -> None:
        """Supprime les ``document_chunks`` d'un Document (réindexation)."""
        ...

    async def add_chunk(
        self,
        *,
        document_id: int,
        chunk_index: int,
        content: str,
        token_count: int,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        """Écrit un ``document_chunks`` avec son embedding (VECTOR)."""
        ...


# ---------------------------------------------------------------------------
# Orchestrateur pur
# ---------------------------------------------------------------------------
class DocumentIngestor:
    """Orchestration pure du pipeline d'ingestion (Exigences 11.1, 11.6).

    Enchaîne : téléchargement → détection de format → extraction → nettoyage →
    déduplication (checksum) → chunking → génération d'embeddings → écriture des
    chunks. Toutes les dépendances sont injectées afin de rester testable sans
    Celery ni base réelle.
    """

    def __init__(
        self,
        store: IngestionStore,
        embedding_provider: EmbeddingProvider,
        *,
        downloader: Downloader | None = None,
        format_detector: FormatDetector | None = None,
        extractor: TextExtractor | None = None,
        cleaner: TextCleaner | None = None,
        deduplicator: Deduplicator | None = None,
        chunker: Chunker | None = None,
        chunk_size_tokens: int = 800,
        chunk_overlap_tokens: int = 120,
    ) -> None:
        self._store = store
        self._embeddings = embedding_provider
        self._downloader = downloader or Downloader()
        self._detector = format_detector or FormatDetector()
        self._extractor = extractor or TextExtractor()
        self._cleaner = cleaner or TextCleaner()
        # Le déduplicateur ne calcule qu'un checksum ; le test « déjà connu » est
        # délégué au store (source de vérité en base).
        self._dedup = deduplicator or Deduplicator()
        self._chunker = chunker or Chunker()
        self._chunk_size_tokens = chunk_size_tokens
        self._chunk_overlap_tokens = chunk_overlap_tokens

    async def ingest(
        self,
        url: str,
        *,
        source_id: int,
        metadata: ChunkMetadata | None = None,
    ) -> IngestionResult:
        """Ingère ``url`` pour ``source_id`` ; idempotent par checksum.

        Retourne un :class:`IngestionResult`. Si le checksum du contenu nettoyé
        est déjà connu, l'ingestion est **ignorée** (``status="skipped"``) sans
        réécriture (Exigence 11.3).
        """
        text, checksum = await self._prepare_checksum_and_text(url)

        existing_id = await self._store.find_document_id_by_checksum(checksum)
        if existing_id is not None:
            _log().info(
                "ingestion_skipped_known_checksum",
                url=url,
                source_id=source_id,
                document_id=existing_id,
                checksum=checksum,
            )
            return IngestionResult(
                status="skipped",
                document_id=existing_id,
                chunk_count=0,
                checksum=checksum,
            )

        document_id = await self._store.create_document(
            source_id=source_id,
            checksum=checksum,
            meta=(metadata.as_dict() if metadata is not None else None),
        )
        chunk_count = await self._chunk_and_store(
            text, document_id=document_id, source_id=source_id, metadata=metadata
        )
        _log().info(
            "ingestion_completed",
            url=url,
            source_id=source_id,
            document_id=document_id,
            chunk_count=chunk_count,
            checksum=checksum,
        )
        return IngestionResult(
            status="ingested",
            document_id=document_id,
            chunk_count=chunk_count,
            checksum=checksum,
        )

    async def reindex(
        self,
        url: str,
        *,
        source_id: int,
        document_id: int,
        metadata: ChunkMetadata | None = None,
    ) -> IngestionResult:
        """Reconstruit les chunks/embeddings d'un Document existant (Exigence 23.1).

        Contrairement à :meth:`ingest`, la réindexation **ignore** l'idempotence
        par checksum : elle supprime les chunks existants puis les régénère (utile
        après un changement de modèle d'embeddings ou de paramètres de chunking).
        """
        text, checksum = await self._prepare_checksum_and_text(url)
        await self._store.delete_chunks(document_id)
        chunk_count = await self._chunk_and_store(
            text, document_id=document_id, source_id=source_id, metadata=metadata
        )
        _log().info(
            "reindex_completed",
            url=url,
            source_id=source_id,
            document_id=document_id,
            chunk_count=chunk_count,
            checksum=checksum,
        )
        return IngestionResult(
            status="reindexed",
            document_id=document_id,
            chunk_count=chunk_count,
            checksum=checksum,
        )

    # -- étapes internes ---------------------------------------------------
    async def _prepare_checksum_and_text(self, url: str) -> tuple[str, str]:
        """Télécharge → détecte → extrait → nettoie → checksum.

        Retourne le couple ``(texte_nettoyé, checksum)``. Ces étapes sont
        déterministes : deux exécutions du même contenu produisent le même
        checksum (base de l'idempotence, Property 5).
        """
        raw = self._downloader.fetch(url)
        fmt = self._detector.detect(raw)
        extracted = self._extractor.extract(raw, fmt)
        cleaned = self._cleaner.clean(extracted)
        checksum = self._dedup.checksum(cleaned)
        return cleaned, checksum

    async def _chunk_and_store(
        self,
        text: str,
        *,
        document_id: int,
        source_id: int,
        metadata: ChunkMetadata | None,
    ) -> int:
        """Découpe ``text``, génère les embeddings et écrit chaque chunk.

        Les métadonnées de chunk sont complétées avec ``source_id`` /
        ``document_id`` (Exigence 11.5). Les embeddings sont calculés en un seul
        appel provider (batch) puis stockés dans ``document_chunks.embedding``
        (VECTOR — Exigence 11.6).
        """
        chunk_meta = self._resolve_metadata(metadata, source_id, document_id)
        chunks: list[Chunk] = self._chunker.chunk(
            text,
            size_tokens=self._chunk_size_tokens,
            overlap_tokens=self._chunk_overlap_tokens,
            metadata=chunk_meta,
        )
        if not chunks:
            return 0

        vectors = self._embeddings.embed([chunk.content for chunk in chunks])
        if len(vectors) != len(chunks):
            raise ValueError(
                "Le provider d'embeddings a renvoyé "
                f"{len(vectors)} vecteurs pour {len(chunks)} chunks."
            )

        for chunk, vector in zip(chunks, vectors, strict=True):
            await self._store.add_chunk(
                document_id=document_id,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                token_count=chunk.token_count,
                embedding=list(vector),
                metadata=chunk.metadata.as_dict(),
            )
        return len(chunks)

    @staticmethod
    def _resolve_metadata(
        metadata: ChunkMetadata | None, source_id: int, document_id: int
    ) -> ChunkMetadata:
        """Complète/annote les métadonnées avec ``source_id`` et ``document_id``."""
        if metadata is None:
            return ChunkMetadata(source_id=source_id, document_id=document_id)
        return ChunkMetadata(
            source_id=metadata.source_id if metadata.source_id is not None else source_id,
            document_id=document_id,
            page=metadata.page,
            section=metadata.section,
            publication_date=metadata.publication_date,
        )


# ---------------------------------------------------------------------------
# Implémentation SQLAlchemy du port (utilisée par les tâches Celery)
# ---------------------------------------------------------------------------
class SqlAlchemyIngestionStore:
    """Implémente :class:`IngestionStore` au-dessus d'une ``AsyncSession``.

    Isolée dans une classe distincte pour que :class:`DocumentIngestor` reste
    testable avec un store en mémoire. Les imports SQLAlchemy/ORM sont locaux
    afin de garder ce module importable dans les tests hors-ligne où la couche
    base est simulée.
    """

    def __init__(self, session: Any) -> None:
        self._session = session

    async def find_document_id_by_checksum(self, checksum: str) -> int | None:
        from sqlalchemy import select

        from app.models.document import Document

        result = await self._session.execute(
            select(Document.id).where(Document.checksum == checksum)
        )
        return result.scalar_one_or_none()

    async def create_document(
        self, *, source_id: int, checksum: str, meta: dict[str, Any] | None
    ) -> int:
        from app.models.document import Document

        document = Document(source_id=source_id, checksum=checksum, meta=meta)
        self._session.add(document)
        await self._session.flush()
        return document.id

    async def delete_chunks(self, document_id: int) -> None:
        from sqlalchemy import delete

        from app.models.document import DocumentChunk

        await self._session.execute(
            delete(DocumentChunk).where(DocumentChunk.document_id == document_id)
        )

    async def add_chunk(
        self,
        *,
        document_id: int,
        chunk_index: int,
        content: str,
        token_count: int,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        from app.models.document import DocumentChunk

        self._session.add(
            DocumentChunk(
                document_id=document_id,
                chunk_index=chunk_index,
                content=content,
                token_count=token_count,
                embedding=embedding,
                chunk_metadata=metadata,
            )
        )


# ---------------------------------------------------------------------------
# Exécution asynchrone d'une ingestion dans une session (utilisé par les tâches)
# ---------------------------------------------------------------------------
async def _run_ingest(
    url: str, source_id: int, *, reindex: bool, document_id: int | None
) -> dict[str, Any]:
    """Ouvre une session, construit l'orchestrateur et exécute (ré)indexation.

    Fonction ``async`` dédiée pour être exécutée par ``asyncio.run`` depuis le
    corps synchrone d'une tâche Celery.
    """
    from app.core.database import SessionLocal
    from app.rag.providers import get_embedding_provider

    async with SessionLocal() as session:
        try:
            store = SqlAlchemyIngestionStore(session)
            ingestor = DocumentIngestor(store, get_embedding_provider())
            if reindex:
                if document_id is None:
                    raise ValueError("reindex_document requiert un document_id")
                result = await ingestor.reindex(
                    url, source_id=source_id, document_id=document_id
                )
            else:
                result = await ingestor.ingest(url, source_id=source_id)
            await session.commit()
            return result.as_dict()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Tâches Celery (enveloppes fines avec réessai/backoff)
# ---------------------------------------------------------------------------
# Paramètres de réessai communs (Exigence 23 — backoff exponentiel).
_RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,       # backoff exponentiel (1s, 2s, 4s, …)
    "retry_backoff_max": 600,    # plafonné à 10 minutes
    "retry_jitter": True,        # dispersion pour éviter les rafales synchronisées
    "max_retries": 5,
}


# Import de l'application Celery. Placé après les définitions pour éviter tout
# cycle et garder l'orchestrateur importable même sans Celery configuré.
from app.workers.celery_app import celery_app  # noqa: E402


@celery_app.task(name="app.workers.ingestion.ingest_document", **_RETRY_KWARGS)
def ingest_document(url: str, source_id: int) -> dict[str, Any]:
    """Tâche orchestrant l'ingestion complète d'un Document (Exigences 11.1, 23.1).

    Enchaîne extraction → chunking → embeddings et écrit les chunks. Idempotente
    par checksum (Exigence 11.3). Réessaie avec backoff exponentiel les erreurs
    transitoires (réseau, provider, base).
    """
    import asyncio

    with _bind_request_id():
        _log().info("task_ingest_document_start", url=url, source_id=source_id)
        return asyncio.run(
            _run_ingest(url, source_id, reindex=False, document_id=None)
        )


@celery_app.task(name="app.workers.ingestion.reindex_document", **_RETRY_KWARGS)
def reindex_document(url: str, source_id: int, document_id: int) -> dict[str, Any]:
    """Reconstruit les chunks/embeddings d'un Document existant (Exigence 23.1).

    Ignore l'idempotence par checksum : supprime puis régénère les chunks.
    """
    import asyncio

    with _bind_request_id():
        _log().info(
            "task_reindex_document_start",
            url=url,
            source_id=source_id,
            document_id=document_id,
        )
        return asyncio.run(
            _run_ingest(url, source_id, reindex=True, document_id=document_id)
        )


@celery_app.task(name="app.workers.ingestion.extract_document", **_RETRY_KWARGS)
def extract_document(url: str) -> dict[str, Any]:
    """Sous-étape : télécharge, détecte le format, extrait et nettoie le texte.

    Exposée comme tâche dédiée (Exigence 23.1) ; retourne le texte nettoyé et son
    checksum, utilisables par ``chunk_document`` / ``generate_embeddings`` dans un
    enchaînement piloté par l'orchestrateur.
    """
    with _bind_request_id():
        downloader = Downloader()
        detector = FormatDetector()
        extractor = TextExtractor()
        cleaner = TextCleaner()
        dedup = Deduplicator()

        raw = downloader.fetch(url)
        fmt = detector.detect(raw)
        text = cleaner.clean(extractor.extract(raw, fmt))
        return {"text": text, "checksum": dedup.checksum(text), "format": fmt.value}


@celery_app.task(name="app.workers.ingestion.chunk_document", **_RETRY_KWARGS)
def chunk_document(
    text: str, source_id: int, document_id: int
) -> list[dict[str, Any]]:
    """Sous-étape : découpe un texte nettoyé en chunks avec métadonnées.

    Retourne une liste de dictionnaires sérialisables (``chunk_index``,
    ``content``, ``token_count``, ``metadata``) prêts pour ``generate_embeddings``.
    """
    with _bind_request_id():
        chunker = Chunker()
        from app.core.config import settings

        meta = ChunkMetadata(source_id=source_id, document_id=document_id)
        chunks = chunker.chunk(
            text,
            size_tokens=settings.rag_chunk_size_tokens,
            overlap_tokens=settings.rag_chunk_overlap_tokens,
            metadata=meta,
        )
        return [
            {
                "chunk_index": chunk.chunk_index,
                "content": chunk.content,
                "token_count": chunk.token_count,
                "metadata": chunk.metadata.as_dict(),
            }
            for chunk in chunks
        ]


@celery_app.task(name="app.workers.ingestion.generate_embeddings", **_RETRY_KWARGS)
def generate_embeddings(texts: list[str]) -> list[list[float]]:
    """Sous-étape : calcule les embeddings d'une liste de textes (Exigence 11.6).

    Délègue au provider configuré (``EMBEDDING_PROVIDER``). Réessaie avec backoff
    en cas d'erreur transitoire du provider.
    """
    with _bind_request_id():
        from app.rag.providers import get_embedding_provider

        provider = get_embedding_provider()
        return [list(vector) for vector in provider.embed(texts)]


__all__ = [
    "IngestionResult",
    "IngestionStore",
    "DocumentIngestor",
    "SqlAlchemyIngestionStore",
    "ingest_document",
    "reindex_document",
    "extract_document",
    "chunk_document",
    "generate_embeddings",
]
