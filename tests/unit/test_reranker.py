"""Tests unitaires de ``Reranker`` (Exigence 12.3).

Couvre :

* la réduction des 20 meilleurs résultats fusionnés aux 6 meilleurs (défaut) ;
* la règle ``keep = min(keep, len(scored))`` : une entrée plus courte est
  renvoyée intégralement ;
* le tri par score décroissant, indépendant de l'ordre d'entrée, avec départage
  déterministe par ``chunk_id`` ;
* les cas limites (``keep == 0``, entrée vide, ``keep`` négatif rejeté).

La logique est pure (aucune base de données) : on construit directement des
:class:`ScoredChunk`.
"""

from __future__ import annotations

import pytest

from app.rag.reranking import DEFAULT_KEEP, Reranker
from app.rag.types import Candidate, ScoredChunk


def _scored(chunk_id: int, score: float) -> ScoredChunk:
    return ScoredChunk(
        candidate=Candidate(chunk_id=chunk_id, content=f"chunk-{chunk_id}"),
        score=score,
    )


@pytest.mark.unit
def test_default_keep_is_six() -> None:
    """La valeur par défaut conserve 6 fragments (Exigence 12.3)."""
    assert DEFAULT_KEEP == 6


@pytest.mark.unit
def test_reduces_twenty_to_six() -> None:
    """20 candidats fusionnés sont réduits aux 6 meilleurs (Exigence 12.3)."""
    scored = [_scored(i, score=float(i)) for i in range(1, 21)]

    kept = Reranker().rerank(scored)

    assert len(kept) == 6
    # Les scores les plus élevés (20..15) sont conservés, ordre décroissant.
    assert [chunk.chunk_id for chunk in kept] == [20, 19, 18, 17, 16, 15]


@pytest.mark.unit
def test_keep_is_min_of_keep_and_size() -> None:
    """Une entrée plus courte que ``keep`` est renvoyée entière (min(keep, size))."""
    scored = [_scored(1, 0.9), _scored(2, 0.5), _scored(3, 0.1)]

    kept = Reranker().rerank(scored, keep=6)

    assert len(kept) == 3
    assert [chunk.chunk_id for chunk in kept] == [1, 2, 3]


@pytest.mark.unit
def test_sorts_by_descending_score_regardless_of_input_order() -> None:
    """Le reranking trie par score décroissant, quel que soit l'ordre d'entrée."""
    scored = [_scored(1, 0.2), _scored(2, 0.9), _scored(3, 0.5)]

    kept = Reranker().rerank(scored, keep=3)

    assert [chunk.chunk_id for chunk in kept] == [2, 3, 1]


@pytest.mark.unit
def test_tie_break_is_deterministic_by_chunk_id() -> None:
    """À score égal, le départage est déterministe par ``chunk_id`` croissant."""
    scored = [_scored(5, 0.5), _scored(2, 0.5), _scored(9, 0.5)]

    kept = Reranker().rerank(scored, keep=2)

    assert [chunk.chunk_id for chunk in kept] == [2, 5]


@pytest.mark.unit
def test_custom_keep_limits_result() -> None:
    """Un ``keep`` personnalisé borne le nombre de résultats retenus."""
    scored = [_scored(i, score=float(i)) for i in range(1, 11)]

    kept = Reranker().rerank(scored, keep=3)

    assert [chunk.chunk_id for chunk in kept] == [10, 9, 8]


@pytest.mark.unit
def test_keep_zero_returns_empty() -> None:
    """``keep == 0`` renvoie une liste vide."""
    scored = [_scored(1, 0.9), _scored(2, 0.5)]

    assert Reranker().rerank(scored, keep=0) == []


@pytest.mark.unit
def test_empty_input_returns_empty() -> None:
    """Une entrée vide produit une sortie vide."""
    assert Reranker().rerank([]) == []


@pytest.mark.unit
def test_negative_keep_is_rejected() -> None:
    """Un ``keep`` négatif est refusé (Exigence 12.3)."""
    with pytest.raises(ValueError, match=">= 0"):
        Reranker().rerank([_scored(1, 0.9)], keep=-1)
