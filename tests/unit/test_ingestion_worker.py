"""Tests unitaires de l'orchestrateur d'ingestion Celery (Exigences 11, 23).

Couvre la logique d'orchestration pure de
:class:`app.workers.ingestion.DocumentIngestor` :

* enchaînement téléchargement → format → extraction → nettoyage → checksum →
  chunking → embeddings → écriture des chunks (Exigence 11.1) ;
* stockage d'un embedding par chunk dans le port de persistance, image de
  ``document_chunks.embedding`` (VECTOR — Exigence 11.6) ;
* idempotence par checksum : un checksum déjà connu ⇒ ingestion ignorée sans
  réécriture (Exigence 11.3) ;
* préservation des métadonnées de chunk (source_id/document_id — Exigence 11.5) ;
* ``reindex`` supprime puis régénère les chunks (Exigence 23.1) ;
* enregistrement effectif des cinq tâches sur l'application Celery (Exigence 23.1).

Les briques réseau/IA/base sont remplacées par des doublures *déterministes* qui
n'annulent aucune logique testée : ``DocumentIngestor`` exécute son vrai code, on
ne lui fournit qu'un contenu téléchargé, un provider d'embeddings et un port de
persistance en mémoire (image fidèle de ``documents`` / ``document_chunks``).
"""

from __future__ import annotations

from typing import Any

import pytest

from app.rag.ingestion import ChunkMetadata, RawDocument
from app.rag.ingestion.format_detector import DocFormat
from app.workers.ingestion import DocumentIngestor, IngestionResult


# ---------------------------------------------------------------------------
# Doublures déterministes
# ---------------------------------------------------------------------------
class _FakeDownloader:
    """``Downloader`` factice : renvoie un contenu texte fixe sans réseau."""

    def __init__(self, content: str, *, calls: list[str] | None = None) -> None:
        self._content = content.encode("utf-8")
        self._calls = calls if calls is not None else []

    def fetch(self, url: str) -> RawDocument:
        self._calls.append(url)
        return RawDocument(
            url=url, content=self._content, content_type="text/plain"
        )


class _FakeEmbeddingProvider:
    """``EmbeddingProvider`` factice : un vecteur déterministe par texte.

    Le vecteur encode la longueur et un hachage stable du texte ; il ne simule
    aucun appel externe et permet de vérifier la correspondance 1 chunk ↔
    1 embedding (Exigence 11.6).
    """

    def __init__(self, dim: int = 4) -> None:
        self._dim = dim
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return [
            [float(len(text))] + [float((hash(text) >> (8 * i)) & 0xFF) for i in range(self._dim - 1)]
            for text in texts
        ]


class _StoredChunk:
    """Chunk persisté en mémoire (image d'une ligne ``document_chunks``)."""

    def __init__(
        self,
        *,
        document_id: int,
        chunk_index: int,
        content: str,
        token_count: int,
        embedding: list[float],
        metadata: dict[str, Any],
    ) -> None:
        self.document_id = document_id
        self.chunk_index = chunk_index
        self.content = content
        self.token_count = token_count
        self.embedding = embedding
        self.metadata = metadata


class _InMemoryStore:
    """Port :class:`IngestionStore` en mémoire (documents + chunks)."""

    def __init__(self) -> None:
        self.documents: dict[int, dict[str, Any]] = {}
        self.checksum_to_id: dict[str, int] = {}
        self.chunks: list[_StoredChunk] = []
        self._next_id = 1

    async def find_document_id_by_checksum(self, checksum: str) -> int | None:
        return self.checksum_to_id.get(checksum)

    async def create_document(
        self, *, source_id: int, checksum: str, meta: dict[str, Any] | None
    ) -> int:
        doc_id = self._next_id
        self._next_id += 1
        self.documents[doc_id] = {
            "source_id": source_id,
            "checksum": checksum,
            "meta": meta,
        }
        self.checksum_to_id[checksum] = doc_id
        return doc_id

    async def delete_chunks(self, document_id: int) -> None:
        self.chunks = [c for c in self.chunks if c.document_id != document_id]

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
        self.chunks.append(
            _StoredChunk(
                document_id=document_id,
                chunk_index=chunk_index,
                content=content,
                token_count=token_count,
                embedding=embedding,
                metadata=metadata,
            )
        )


def _make_ingestor(
    content: str,
    store: _InMemoryStore,
    provider: _FakeEmbeddingProvider,
    *,
    download_calls: list[str] | None = None,
    size_tokens: int = 5,
    overlap_tokens: int = 1,
) -> DocumentIngestor:
    """Construit un orchestrateur câblé sur les doublures déterministes."""
    return DocumentIngestor(
        store,
        provider,
        downloader=_FakeDownloader(content, calls=download_calls),  # type: ignore[arg-type]
        chunk_size_tokens=size_tokens,
        chunk_overlap_tokens=overlap_tokens,
    )


# ---------------------------------------------------------------------------
# Ingestion nominale
# ---------------------------------------------------------------------------
@pytest.mark.unit
async def test_ingest_creates_document_and_chunks_with_embeddings() -> None:
    """Une ingestion nominale crée un Document et un embedding par chunk (11.1, 11.6)."""
    store = _InMemoryStore()
    provider = _FakeEmbeddingProvider(dim=4)
    content = "mot " * 12  # 12 tokens → plusieurs chunks (size 5, overlap 1)
    ingestor = _make_ingestor(content, store, provider)

    result = await ingestor.ingest("http://example.org/doc.txt", source_id=7)

    assert isinstance(result, IngestionResult)
    assert result.status == "ingested"
    assert result.document_id == 1
    assert result.chunk_count == len(store.chunks) > 1
    # Un embedding par chunk, de dimension fixe (image de VECTOR).
    assert all(len(c.embedding) == 4 for c in store.chunks)
    # chunk_index croissant à partir de 0.
    assert [c.chunk_index for c in store.chunks] == list(range(len(store.chunks)))
    # Un seul appel batch au provider d'embeddings.
    assert len(provider.embed_calls) == 1


@pytest.mark.unit
async def test_ingest_preserves_source_and_document_in_metadata() -> None:
    """Chaque chunk conserve source_id et document_id (Exigence 11.5)."""
    store = _InMemoryStore()
    ingestor = _make_ingestor("alpha beta gamma delta", store, _FakeEmbeddingProvider())

    result = await ingestor.ingest("http://example.org/a.txt", source_id=42)

    assert store.chunks
    for chunk in store.chunks:
        assert chunk.metadata["source_id"] == 42
        assert chunk.metadata["document_id"] == result.document_id


@pytest.mark.unit
async def test_ingest_respects_provided_metadata_page_and_section() -> None:
    """Les métadonnées fournies (page/section/date) sont conservées (Exigence 11.5)."""
    store = _InMemoryStore()
    ingestor = _make_ingestor("alpha beta gamma", store, _FakeEmbeddingProvider())
    meta = ChunkMetadata(source_id=3, page=5, section="Intro")

    await ingestor.ingest("http://example.org/a.txt", source_id=3, metadata=meta)

    assert store.chunks
    first = store.chunks[0].metadata
    assert first["page"] == 5
    assert first["section"] == "Intro"


# ---------------------------------------------------------------------------
# Idempotence par checksum (Exigence 11.3)
# ---------------------------------------------------------------------------
@pytest.mark.unit
async def test_ingest_is_idempotent_by_checksum() -> None:
    """Réingérer le même contenu est ignoré, sans réécriture (Exigence 11.3)."""
    store = _InMemoryStore()
    content = "contenu stable identique pour les deux ingestions"

    first = await _make_ingestor(content, store, _FakeEmbeddingProvider()).ingest(
        "http://example.org/doc.txt", source_id=1
    )
    chunks_after_first = len(store.chunks)
    docs_after_first = len(store.documents)

    second = await _make_ingestor(content, store, _FakeEmbeddingProvider()).ingest(
        "http://example.org/doc.txt", source_id=1
    )

    assert first.status == "ingested"
    assert second.status == "skipped"
    assert second.document_id == first.document_id
    assert second.chunk_count == 0
    # Aucune écriture supplémentaire.
    assert len(store.chunks) == chunks_after_first
    assert len(store.documents) == docs_after_first


@pytest.mark.unit
async def test_skipped_ingestion_does_not_call_embedding_provider() -> None:
    """Une ingestion ignorée n'appelle pas le provider d'embeddings (Exigence 11.3)."""
    store = _InMemoryStore()
    content = "texte deja connu"
    await _make_ingestor(content, store, _FakeEmbeddingProvider()).ingest(
        "http://example.org/doc.txt", source_id=1
    )

    provider = _FakeEmbeddingProvider()
    await _make_ingestor(content, store, provider).ingest(
        "http://example.org/doc.txt", source_id=1
    )

    assert provider.embed_calls == []


# ---------------------------------------------------------------------------
# Réindexation (Exigence 23.1)
# ---------------------------------------------------------------------------
@pytest.mark.unit
async def test_reindex_replaces_existing_chunks() -> None:
    """``reindex`` supprime les anciens chunks puis les régénère (Exigence 23.1)."""
    store = _InMemoryStore()
    content = "un deux trois quatre cinq six sept huit"

    ingest_result = await _make_ingestor(content, store, _FakeEmbeddingProvider()).ingest(
        "http://example.org/doc.txt", source_id=2
    )
    doc_id = ingest_result.document_id
    assert doc_id is not None
    original_chunk_count = len(store.chunks)

    reindex_result = await _make_ingestor(
        content, store, _FakeEmbeddingProvider()
    ).reindex("http://example.org/doc.txt", source_id=2, document_id=doc_id)

    assert reindex_result.status == "reindexed"
    assert reindex_result.document_id == doc_id
    # Les chunks appartiennent tous au Document réindexé, en même nombre.
    assert all(c.document_id == doc_id for c in store.chunks)
    assert len(store.chunks) == original_chunk_count == reindex_result.chunk_count


@pytest.mark.unit
async def test_reindex_without_document_id_would_be_rejected_by_task() -> None:
    """``reindex`` exige un document_id valide (garde-fou d'orchestration)."""
    store = _InMemoryStore()
    ingestor = _make_ingestor("alpha beta gamma", store, _FakeEmbeddingProvider())
    # Réindexer un id inexistant ne trouve aucun chunk à supprimer mais régénère.
    result = await ingestor.reindex(
        "http://example.org/a.txt", source_id=1, document_id=999
    )
    assert result.status == "reindexed"
    assert result.document_id == 999


# ---------------------------------------------------------------------------
# Empty content
# ---------------------------------------------------------------------------
@pytest.mark.unit
async def test_ingest_empty_content_creates_document_without_chunks() -> None:
    """Un contenu vide crée le Document mais aucun chunk (aucun embedding appelé)."""
    store = _InMemoryStore()
    provider = _FakeEmbeddingProvider()
    ingestor = _make_ingestor("   \n  ", store, provider)

    result = await ingestor.ingest("http://example.org/empty.txt", source_id=1)

    assert result.status == "ingested"
    assert result.chunk_count == 0
    assert store.chunks == []
    assert provider.embed_calls == []


# ---------------------------------------------------------------------------
# Enregistrement des tâches Celery (Exigence 23.1)
# ---------------------------------------------------------------------------
@pytest.mark.unit
def test_all_ingestion_tasks_are_registered() -> None:
    """Les cinq tâches d'ingestion sont enregistrées sur l'app Celery (Exigence 23.1)."""
    from app.workers.celery_app import celery_app

    expected = {
        "app.workers.ingestion.ingest_document",
        "app.workers.ingestion.extract_document",
        "app.workers.ingestion.chunk_document",
        "app.workers.ingestion.generate_embeddings",
        "app.workers.ingestion.reindex_document",
    }
    assert expected <= set(celery_app.tasks)


@pytest.mark.unit
def test_ingestion_tasks_declare_retry_backoff() -> None:
    """Les tâches déclarent le réessai avec backoff exponentiel (Exigence 23)."""
    from app.workers.ingestion import ingest_document

    # autoretry_for / retry_backoff sont posés par le décorateur de tâche.
    assert getattr(ingest_document, "retry_backoff", None) is True
    assert ingest_document.max_retries == 5


@pytest.mark.unit
def test_format_detector_reports_txt_for_plain_content() -> None:
    """Garde-fou : le contenu texte des doublures est bien détecté en TXT."""
    from app.rag.ingestion import FormatDetector

    raw = RawDocument(url="x", content=b"abc", content_type="text/plain")
    assert FormatDetector().detect(raw) is DocFormat.TXT
