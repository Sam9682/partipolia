"""Tests unitaires du chaînage récupération → classement RAG (Exigences 12, 13.8).

Complète les tests par module (``test_hybrid_search.py``, ``test_score_fusion.py``,
``test_reranker.py``, contexte dans ``tests/rag/test_rag_understanding.py``) en
exerçant leur **enchaînement** tel qu'il est câblé dans le pipeline : la recherche
hybride produit des :class:`Candidate`, la fusion (poids sommant à 1.0) les ordonne,
le reranking réduit les 20 meilleurs aux 6, et la construction de contexte émet des
citations numérotées par ordre décroissant de score. Le cas « information
insuffisante » (retrieval vide) est vérifié au niveau de la chaîne récupération →
classement (Exigence 13.8).

La logique testée est celle du domaine ; seules les frontières externes (accès
base via ``AsyncSession`` et ``EmbeddingProvider``) sont remplacées par des
doublures, conformément à ``test_hybrid_search.py``. Aucune logique métier n'est
mockée : ``ScoreFusion``, ``Reranker`` et ``ContextBuilder`` sont les vrais.
"""

from __future__ import annotations

import pytest

from app.rag.context import ContextBuilder
from app.rag.ranking import ScoreFusion
from app.rag.reranking import Reranker
from app.rag.retriever import HybridSearch
from app.rag.types import Candidate


# --------------------------------------------------------------------------- #
# Doublures de frontière (identiques à test_hybrid_search.py)                 #
# --------------------------------------------------------------------------- #
class _FakeMappings:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def all(self) -> list[dict[str, object]]:
        return self._rows


class _FakeResult:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self._rows)


class _FakeSession:
    """``AsyncSession`` factice : distingue voie lexicale (plainto_tsquery) et sémantique (<=>)."""

    def __init__(
        self,
        lexical_rows: list[dict[str, object]],
        semantic_rows: list[dict[str, object]],
    ) -> None:
        self._lexical_rows = lexical_rows
        self._semantic_rows = semantic_rows

    async def execute(self, statement: object, _params: object = None) -> _FakeResult:
        sql = str(statement)
        if "plainto_tsquery" in sql:
            return _FakeResult(self._lexical_rows)
        if "<=>" in sql:
            return _FakeResult(self._semantic_rows)
        raise AssertionError(f"Requête inattendue : {sql!r}")


class _FakeEmbeddingProvider:
    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


def _row(
    chunk_id: int,
    *,
    rank: float | None = None,
    distance: float | None = None,
    source_type: str = "OFFICIAL",
    is_verified: bool = True,
    publication_date: str | None = None,
    content: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "chunk_id": chunk_id,
        "content": content if content is not None else f"contenu-{chunk_id}",
        "document_id": 100 + chunk_id,
        "source_id": 200 + chunk_id,
        "source_type": source_type,
        "is_verified": is_verified,
        "publication_date": publication_date,
    }
    if rank is not None:
        row["rank"] = rank
    if distance is not None:
        row["distance"] = distance
    return row


def _search(
    lexical_rows: list[dict[str, object]],
    semantic_rows: list[dict[str, object]],
    *,
    vector: list[float] | None = None,
) -> HybridSearch:
    return HybridSearch(
        _FakeSession(lexical_rows, semantic_rows),  # type: ignore[arg-type]
        _FakeEmbeddingProvider(vector),
    )


def _candidate(chunk_id: int, *, semantic: float, content: str = "") -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        content=content or f"chunk-{chunk_id}",
        semantic=semantic,
    )


# --------------------------------------------------------------------------- #
# Chaîne récupération → fusion → reranking → contexte (Exigences 12.1–12.3)   #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_retrieve_fuse_rerank_context_descending_order() -> None:
    """La chaîne complète produit un contexte ordonné par score décroissant (Exigence 12)."""
    # Trois fragments à pertinences lexicales différentes ; le meilleur rang
    # lexical (chunk 3) doit finir en tête après fusion et reranking.
    lexical = [
        _row(1, rank=0.2, content="faible pertinence"),
        _row(2, rank=0.6, content="pertinence moyenne"),
        _row(3, rank=1.0, content="forte pertinence"),
    ]
    candidates = await _search(lexical, []).search("réforme fiscale", top_k=20)

    scored = ScoreFusion().fuse(candidates)
    top = Reranker().rerank(scored, keep=6)
    context, citations = ContextBuilder().build(top)

    # Ordre décroissant de score garanti par la fusion + le reranking.
    scores = [item.score for item in top]
    assert scores == sorted(scores, reverse=True)
    # Le fragment le plus pertinent est cité en premier (numéro 1).
    assert citations[0].chunk_id == 3
    assert [c.number for c in citations] == [1, 2, 3]
    # Le contexte reflète l'ordre décroissant et numérote sans trou.
    assert context.index("[1] forte pertinence") < context.index("[2] pertinence moyenne")
    assert "[3] faible pertinence" in context


@pytest.mark.unit
async def test_retrieval_twenty_candidates_reranked_to_six_in_order() -> None:
    """20 candidats récupérés sont réduits aux 6 meilleurs, ordre décroissant (Exigence 12.3)."""
    # 20 fragments sémantiques de distances croissantes ⇒ similarités décroissantes.
    semantic = [_row(i, distance=(i - 1) * 0.05) for i in range(1, 21)]
    candidates = await _search([], semantic).search("europe", top_k=20)
    assert len(candidates) == 20

    scored = ScoreFusion().fuse(candidates)
    top = Reranker().rerank(scored, keep=6)

    assert len(top) == 6
    # Les 6 plus proches (distances les plus faibles : chunks 1..6) sont retenus,
    # par score décroissant.
    assert [chunk.chunk_id for chunk in top] == [1, 2, 3, 4, 5, 6]
    scores = [item.score for item in top]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.unit
async def test_context_strips_content_and_propagates_metadata() -> None:
    """Le contexte nettoie le contenu et propage les métadonnées de Source (Exigence 13.4)."""
    lexical = [_row(1, rank=0.9, content="  passage avec espaces  ", publication_date="2024-01-01")]
    candidates = await _search(lexical, []).search("climat", top_k=20)

    top = Reranker().rerank(ScoreFusion().fuse(candidates))
    context, citations = ContextBuilder().build(top)

    citation = citations[0]
    # Contenu ébarbé côté citation et côté contexte (pas d'espaces parasites).
    assert citation.content == "passage avec espaces"
    assert "[1] passage avec espaces" in context
    # Les identifiants de Source/document issus des métadonnées sont propagés.
    assert citation.source_id == 201
    assert citation.document_id == 101
    assert citation.metadata is not None
    assert citation.metadata["source_type"] == "OFFICIAL"


# --------------------------------------------------------------------------- #
# « Information insuffisante » : retrieval vide ⇒ chaîne vide (Exigence 13.8)  #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
async def test_empty_retrieval_yields_empty_context_for_insufficient_information() -> None:
    """Aucune Source trouvée ⇒ contexte vide (déclencheur « information insuffisante », 13.8)."""
    # Ni voie lexicale ni voie sémantique ne remontent de fragment.
    candidates = await _search([], []).search("sujet sans source", top_k=20)
    assert candidates == []

    top = Reranker().rerank(ScoreFusion().fuse(candidates), keep=6)
    context, citations = ContextBuilder().build(top)

    # Contexte vide et aucune citation : le pipeline bascule alors sur
    # « information insuffisante » sans appeler la génération (Exigences 13.8, 14.6).
    assert top == []
    assert context == ""
    assert citations == []


@pytest.mark.unit
async def test_blank_query_short_circuits_retrieval_to_empty() -> None:
    """Une requête vide n'exécute aucune recherche et ne produit aucun contexte (Exigence 13.8)."""
    candidates = await _search([_row(1, rank=1.0)], []).search("   ", top_k=20)
    assert candidates == []

    context, citations = ContextBuilder().build(
        Reranker().rerank(ScoreFusion().fuse(candidates))
    )
    assert context == ""
    assert citations == []


@pytest.mark.unit
async def test_reranker_keep_zero_forces_empty_context() -> None:
    """Un reranking conservant 0 fragment produit un contexte vide (Exigence 13.8)."""
    lexical = [_row(1, rank=0.9), _row(2, rank=0.5)]
    candidates = await _search(lexical, []).search("budget", top_k=20)

    top = Reranker().rerank(ScoreFusion().fuse(candidates), keep=0)
    context, citations = ContextBuilder().build(top)

    assert top == []
    assert context == ""
    assert citations == []


# --------------------------------------------------------------------------- #
# Fusion : combinaison convexe (poids = 1.0) sur la chaîne (Exigence 12.2)     #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_fusion_preserves_convexity_across_chain() -> None:
    """Poids sommant à 1.0 ⇒ scores fusionnés bornés dans [0, 1] tout au long (Exigence 12.2)."""
    fusion = ScoreFusion()
    assert sum(fusion.weights.values()) == pytest.approx(1.0)

    candidates = [
        _candidate(1, semantic=0.9),
        _candidate(2, semantic=0.4),
        _candidate(3, semantic=0.7),
    ]
    top = Reranker().rerank(fusion.fuse(candidates), keep=6)

    assert all(0.0 <= item.score <= 1.0 for item in top)
    assert [item.chunk_id for item in top] == [1, 3, 2]
