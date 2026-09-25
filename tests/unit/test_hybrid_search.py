"""Tests unitaires de ``HybridSearch`` (Exigence 12.1).

Couvre :

* la combinaison des voies lexicale (tsvector/tsquery) et sémantique (pgvector,
  distance cosinus) et leur fusion par ``chunk_id`` ;
* la normalisation des sous-scores ``semantic`` / ``lexical`` dans ``[0, 1]`` ;
* la dérivation de ``source_quality`` (type + vérification) et ``recency``
  (date de publication) ;
* les cas limites : requête vide, ``top_k`` non positif, embedding vide.

La logique testée est celle du Service ; seules la couche d'accès aux données
(``AsyncSession.execute`` renvoyant les lignes qu'une vraie requête SQL
produirait) et l'``EmbeddingProvider`` sont remplacés par des doublures. Aucune
logique métier n'est mockée.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.rag.retriever import (
    HybridSearch,
    _recency_score,
    _source_quality_score,
)


class _FakeMappings:
    """Expose ``all()`` renvoyant une liste de mappings (lignes SQL)."""

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
    """``AsyncSession`` factice distinguant requête lexicale et sémantique.

    Le SQL lexical contient ``plainto_tsquery`` ; le SQL sémantique contient
    l'opérateur de distance cosinus ``<=>``. On renvoie le jeu de lignes
    correspondant à chaque voie.
    """

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
    """``EmbeddingProvider`` factice renvoyant un vecteur fixe (ou vide)."""

    def __init__(self, vector: list[float] | None = None) -> None:
        self._vector = vector if vector is not None else [0.1, 0.2, 0.3]

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


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


def _row(
    chunk_id: int,
    *,
    rank: float | None = None,
    distance: float | None = None,
    source_type: str = "OFFICIAL",
    is_verified: bool = True,
    publication_date: str | None = None,
) -> dict[str, object]:
    row: dict[str, object] = {
        "chunk_id": chunk_id,
        "content": f"contenu-{chunk_id}",
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


@pytest.mark.unit
async def test_empty_query_returns_empty() -> None:
    """Une requête vide/blanche renvoie une liste vide (Exigence 12.1)."""
    search = _search([], [])
    assert await search.search("   ", top_k=10) == []


@pytest.mark.unit
async def test_non_positive_top_k_returns_empty() -> None:
    """Un ``top_k`` non positif renvoie une liste vide."""
    search = _search([], [])
    assert await search.search("réforme", top_k=0) == []


@pytest.mark.unit
async def test_empty_embedding_skips_semantic_but_keeps_lexical() -> None:
    """Un embedding vide n'annule pas la voie lexicale (Exigence 12.1)."""
    search = _search([_row(1, rank=0.8)], [], vector=[])
    candidates = await search.search("réforme", top_k=10)

    assert len(candidates) == 1
    assert candidates[0].chunk_id == 1
    assert candidates[0].lexical == pytest.approx(1.0)
    assert candidates[0].semantic == 0.0


@pytest.mark.unit
async def test_merges_lexical_and_semantic_by_chunk_id() -> None:
    """Un fragment retrouvé par les deux voies conserve ses deux sous-scores."""
    lexical = [_row(1, rank=0.9), _row(2, rank=0.3)]
    semantic = [_row(1, distance=0.1), _row(3, distance=0.4)]
    search = _search(lexical, semantic)

    candidates = await search.search("réforme fiscale", top_k=10)
    by_id = {c.chunk_id: c for c in candidates}

    # Fragments distincts fusionnés : 1 (les deux voies), 2 (lexical), 3 (sémantique).
    assert set(by_id) == {1, 2, 3}
    # 1 a un score lexical (0.9 normalisé au max ⇒ 1.0) et un score sémantique.
    assert by_id[1].lexical == pytest.approx(1.0)
    assert by_id[1].semantic > 0.0
    # 2 n'a pas de score sémantique ; 3 n'a pas de score lexical.
    assert by_id[2].semantic == 0.0
    assert by_id[3].lexical == 0.0


@pytest.mark.unit
async def test_lexical_scores_normalized_to_unit_interval() -> None:
    """Le meilleur rang lexical est normalisé à 1.0, les autres proportionnels."""
    lexical = [_row(1, rank=2.0), _row(2, rank=1.0)]
    search = _search(lexical, [])

    candidates = await search.search("budget", top_k=10)
    by_id = {c.chunk_id: c for c in candidates}

    assert by_id[1].lexical == pytest.approx(1.0)
    assert by_id[2].lexical == pytest.approx(0.5)


@pytest.mark.unit
async def test_semantic_score_from_cosine_distance() -> None:
    """La similarité sémantique dérive de ``1 - distance`` puis est normalisée."""
    # distance 0.0 ⇒ similarité 1.0 (max) ; distance 0.5 ⇒ 0.5, normalisé /1.0.
    semantic = [_row(1, distance=0.0), _row(2, distance=0.5)]
    search = _search([], semantic)

    candidates = await search.search("europe", top_k=10)
    by_id = {c.chunk_id: c for c in candidates}

    assert by_id[1].semantic == pytest.approx(1.0)
    assert by_id[2].semantic == pytest.approx(0.5)


@pytest.mark.unit
async def test_candidates_carry_metadata() -> None:
    """Chaque candidat porte contenu et métadonnées (source, document)."""
    search = _search([_row(1, rank=0.5, publication_date="2024-01-01")], [])

    candidates = await search.search("climat", top_k=10)
    candidate = candidates[0]

    assert candidate.content == "contenu-1"
    assert candidate.metadata is not None
    assert candidate.metadata["document_id"] == 101
    assert candidate.metadata["source_id"] == 201
    assert candidate.metadata["source_type"] == "OFFICIAL"


# --- Helpers purs ---------------------------------------------------------


@pytest.mark.unit
def test_source_quality_verified_official_is_high() -> None:
    """Une Source officielle vérifiée obtient la qualité maximale."""
    assert _source_quality_score("OFFICIAL", True) == pytest.approx(1.0)


@pytest.mark.unit
def test_source_quality_unverified_is_penalized() -> None:
    """Une Source non vérifiée est pénalisée par rapport à une vérifiée."""
    assert _source_quality_score("OFFICIAL", False) == pytest.approx(0.8)


@pytest.mark.unit
def test_source_quality_unknown_type_uses_default() -> None:
    """Un type inconnu (ou absent) retombe sur la qualité par défaut."""
    assert _source_quality_score(None, True) == pytest.approx(0.5)


@pytest.mark.unit
def test_recency_today_is_one_and_missing_is_zero() -> None:
    """Une publication du jour donne 1.0 ; une date absente donne 0.0."""
    assert _recency_score(date.today()) == pytest.approx(1.0)
    assert _recency_score(None) == 0.0


@pytest.mark.unit
def test_recency_decreases_with_age() -> None:
    """La récence décroît avec l'ancienneté et reste bornée à [0, 1]."""
    old = date.today() - timedelta(days=365)
    score = _recency_score(old)

    assert 0.0 < score < 1.0


@pytest.mark.unit
def test_recency_accepts_iso_string() -> None:
    """Une date ISO sous forme de chaîne est acceptée."""
    assert _recency_score(date.today().isoformat()) == pytest.approx(1.0)
    assert _recency_score("not-a-date") == 0.0
