"""Tests unitaires de ``ScoreFusion`` (Exigence 12.2, Property 8).

Couvre :

* la combinaison pondérée ``0.40 sémantique + 0.30 lexical + 0.20 qualité +
  0.10 récence`` sur les sous-scores d'un :class:`Candidate` (Exigence 12.2) ;
* le rejet de poids dont la somme s'écarte de 1.0 (Property 8) ;
* le rejet de poids manquants ou inconnus ;
* le tri des résultats par score décroissant, départage déterministe par
  ``chunk_id`` ;
* la bornitude du score fusionné dans ``[0, 1]`` quand les sous-scores le sont
  (combinaison convexe).

La logique est pure (aucune base de données) : on construit directement des
:class:`Candidate`.
"""

from __future__ import annotations

import pytest

from app.rag.ranking import ScoreFusion
from app.rag.types import Candidate


def _candidate(
    chunk_id: int,
    *,
    semantic: float = 0.0,
    lexical: float = 0.0,
    source_quality: float = 0.0,
    recency: float = 0.0,
) -> Candidate:
    return Candidate(
        chunk_id=chunk_id,
        content=f"chunk-{chunk_id}",
        semantic=semantic,
        lexical=lexical,
        source_quality=source_quality,
        recency=recency,
    )


@pytest.mark.unit
def test_default_weights_match_design() -> None:
    """Les poids par défaut sont ceux de la conception (Exigence 12.2)."""
    fusion = ScoreFusion()

    assert fusion.weights == {
        "semantic": 0.40,
        "lexical": 0.30,
        "source_quality": 0.20,
        "recency": 0.10,
    }


@pytest.mark.unit
def test_score_is_weighted_combination() -> None:
    """Le score applique la combinaison pondérée attendue (Exigence 12.2)."""
    fusion = ScoreFusion()
    candidate = _candidate(
        1, semantic=1.0, lexical=0.5, source_quality=0.25, recency=0.0
    )

    # 0.40*1.0 + 0.30*0.5 + 0.20*0.25 + 0.10*0.0 = 0.40 + 0.15 + 0.05 = 0.60
    assert fusion.score(candidate) == pytest.approx(0.60)


@pytest.mark.unit
def test_all_ones_yields_one() -> None:
    """Des sous-scores tous à 1.0 donnent un score fusionné de 1.0 (poids somment à 1)."""
    fusion = ScoreFusion()
    candidate = _candidate(
        1, semantic=1.0, lexical=1.0, source_quality=1.0, recency=1.0
    )

    assert fusion.score(candidate) == pytest.approx(1.0)


@pytest.mark.unit
def test_fuse_sorts_by_descending_score() -> None:
    """``fuse`` trie les résultats par score décroissant (Exigence 12.2)."""
    fusion = ScoreFusion()
    candidates = [
        _candidate(1, semantic=0.1),
        _candidate(2, semantic=0.9),
        _candidate(3, semantic=0.5),
    ]

    scored = fusion.fuse(candidates)

    assert [item.chunk_id for item in scored] == [2, 3, 1]
    assert scored[0].score >= scored[1].score >= scored[2].score


@pytest.mark.unit
def test_fuse_tie_break_is_deterministic_by_chunk_id() -> None:
    """À score égal, le départage est déterministe par ``chunk_id`` croissant."""
    fusion = ScoreFusion()
    candidates = [
        _candidate(5, semantic=0.5),
        _candidate(2, semantic=0.5),
        _candidate(8, semantic=0.5),
    ]

    scored = fusion.fuse(candidates)

    assert [item.chunk_id for item in scored] == [2, 5, 8]


@pytest.mark.unit
def test_fuse_empty_returns_empty() -> None:
    """Une entrée vide produit une sortie vide."""
    assert ScoreFusion().fuse([]) == []


@pytest.mark.unit
def test_weights_not_summing_to_one_are_rejected() -> None:
    """Des poids dont la somme n'est pas 1.0 sont refusés (Property 8)."""
    with pytest.raises(ValueError, match="somme des poids"):
        ScoreFusion(
            {
                "semantic": 0.5,
                "lexical": 0.5,
                "source_quality": 0.5,
                "recency": 0.5,
            }
        )


@pytest.mark.unit
def test_missing_weight_component_is_rejected() -> None:
    """Un poids manquant est refusé (couverture des quatre composantes)."""
    with pytest.raises(ValueError, match="manquants"):
        ScoreFusion({"semantic": 0.6, "lexical": 0.4})


@pytest.mark.unit
def test_unknown_weight_component_is_rejected() -> None:
    """Un poids inconnu est refusé."""
    with pytest.raises(ValueError, match="inconnus"):
        ScoreFusion(
            {
                "semantic": 0.40,
                "lexical": 0.30,
                "source_quality": 0.20,
                "recency": 0.05,
                "bogus": 0.05,
            }
        )


@pytest.mark.unit
def test_custom_valid_weights_are_used() -> None:
    """Des poids personnalisés valides (somme 1.0) sont appliqués."""
    fusion = ScoreFusion(
        {
            "semantic": 0.25,
            "lexical": 0.25,
            "source_quality": 0.25,
            "recency": 0.25,
        }
    )
    candidate = _candidate(
        1, semantic=1.0, lexical=0.0, source_quality=1.0, recency=0.0
    )

    assert fusion.score(candidate) == pytest.approx(0.5)


@pytest.mark.unit
def test_fused_score_within_unit_interval_for_normalized_subscores() -> None:
    """Sous-scores dans [0,1] ⇒ score fusionné dans [0,1] (combinaison convexe)."""
    fusion = ScoreFusion()
    candidate = _candidate(
        1, semantic=0.7, lexical=0.3, source_quality=0.9, recency=0.2
    )

    score = fusion.score(candidate)

    assert 0.0 <= score <= 1.0
