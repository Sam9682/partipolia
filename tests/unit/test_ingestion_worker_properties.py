"""Tests de propriété de l'orchestrateur d'ingestion (Exigences 11.1, 11.3, 11.6).

Ces propriétés renforcent les tests par l'exemple de ``test_ingestion_worker`` en
vérifiant, sur des contenus générés aléatoirement (Hypothesis), les invariants
d'orchestration de :class:`app.workers.ingestion.DocumentIngestor` :

* **Idempotence par checksum** : ré-ingérer un contenu déjà indexé n'écrit rien
  de plus et renvoie ``skipped`` (Exigence 11.3).
* **Correspondance chunk ↔ embedding** : le nombre d'embeddings écrits égale le
  nombre de chunks, chacun de dimension fixe (image de ``document_chunks.embedding``
  VECTOR — Exigence 11.6).

Remarque : la tâche 6.6 n'est pas l'une des 10 Correctness Properties numérotées
(pas d'étiquette ``Property N``) ; ces propriétés sont des tests de robustesse de
l'orchestration et n'utilisent pas le suivi PBT dédié.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.ingestion import RawDocument
from app.workers.ingestion import DocumentIngestor


class _FakeDownloader:
    def __init__(self, content: str) -> None:
        self._content = content.encode("utf-8")

    def fetch(self, url: str) -> RawDocument:
        return RawDocument(url=url, content=self._content, content_type="text/plain")


class _FakeEmbeddingProvider:
    def __init__(self, dim: int = 3) -> None:
        self._dim = dim
        self.embed_calls = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls += 1
        return [[float(len(t))] * self._dim for t in texts]


class _InMemoryStore:
    def __init__(self) -> None:
        self.checksum_to_id: dict[str, int] = {}
        self.chunks: list[dict[str, object]] = []
        self._next_id = 1

    async def find_document_id_by_checksum(self, checksum: str) -> int | None:
        return self.checksum_to_id.get(checksum)

    async def create_document(self, *, source_id, checksum, meta) -> int:  # type: ignore[no-untyped-def]
        doc_id = self._next_id
        self._next_id += 1
        self.checksum_to_id[checksum] = doc_id
        return doc_id

    async def delete_chunks(self, document_id: int) -> None:
        self.chunks = [c for c in self.chunks if c["document_id"] != document_id]

    async def add_chunk(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        self.chunks.append(kwargs)


def _ingestor(content: str, store: _InMemoryStore, provider: _FakeEmbeddingProvider) -> DocumentIngestor:
    return DocumentIngestor(
        store,
        provider,  # type: ignore[arg-type]
        downloader=_FakeDownloader(content),  # type: ignore[arg-type]
        chunk_size_tokens=4,
        chunk_overlap_tokens=1,
    )


@pytest.mark.unit
@settings(max_examples=120)
@given(content=st.text(min_size=1, max_size=200))
def test_reingesting_same_content_is_idempotent(content: str) -> None:
    """Deux ingestions du même contenu ⇒ une seule écriture (Exigence 11.3)."""

    async def scenario() -> None:
        store = _InMemoryStore()
        first = await _ingestor(content, store, _FakeEmbeddingProvider()).ingest(
            "http://example.org/doc", source_id=1
        )
        chunks_after_first = len(store.chunks)

        provider = _FakeEmbeddingProvider()
        second = await _ingestor(content, store, provider).ingest(
            "http://example.org/doc", source_id=1
        )

        # La seconde ingestion ne réécrit rien et pointe le même Document.
        assert second.document_id == first.document_id
        assert len(store.chunks) == chunks_after_first
        assert second.chunk_count == 0
        # Idempotence : aucune génération d'embeddings lors du second passage.
        assert provider.embed_calls == 0
        # Le statut dépend de la présence de tokens dans le contenu.
        if first.chunk_count > 0 or first.status == "ingested":
            assert second.status == "skipped"

    asyncio.run(scenario())


@pytest.mark.unit
@settings(max_examples=120)
@given(content=st.text(min_size=1, max_size=200))
def test_one_embedding_per_chunk_of_fixed_dimension(content: str) -> None:
    """Autant d'embeddings que de chunks, chacun de dimension fixe (Exigence 11.6)."""

    async def scenario() -> None:
        store = _InMemoryStore()
        result = await _ingestor(content, store, _FakeEmbeddingProvider(dim=3)).ingest(
            "http://example.org/doc", source_id=1
        )
        assert len(store.chunks) == result.chunk_count
        assert all(len(c["embedding"]) == 3 for c in store.chunks)  # type: ignore[arg-type]

    asyncio.run(scenario())
