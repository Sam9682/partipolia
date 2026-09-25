# Feature: partipolia-platform, Property 9: Reranking — exactement 6 parmi les 20 meilleurs
"""Test de propriété du ``Reranker`` (Exigence 12.3).

**Property 9: Reranking — exactement 6 parmi les 20 meilleurs**

**Validates: Requirements 12.3**

Pour toute liste de :class:`~app.rag.types.ScoredChunk` :

* ``rerank`` renvoie exactement ``min(6, len(entrée))`` éléments — une liste plus
  courte que 6 est renvoyée intégralement, sans complétion ;
* tous les éléments renvoyés sont **issus de l'entrée** (aucun fragment inventé) ;
* le résultat est ordonné par **score décroissant** ;
* lorsque l'entrée compte au moins 6 éléments, ``rerank`` retient exactement les
  6 fragments au **score le plus élevé** (départage déterministe par ``chunk_id``
  croissant, cohérent avec la fusion).

Le test exerce le vrai code du module (tri pur), sans dépendance externe : aucun
accès réseau ni base n'est requis, il est donc valable hors-ligne.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.reranking import DEFAULT_KEEP, Reranker
from app.rag.types import Candidate, ScoredChunk


def _scored(chunk_id: int, score: float) -> ScoredChunk:
    """Construit un ``ScoredChunk`` minimal pour le test."""
    return ScoredChunk(
        candidate=Candidate(chunk_id=chunk_id, content=f"chunk-{chunk_id}"),
        score=score,
    )


# Listes de ScoredChunk aux ``chunk_id`` uniques (identité stable des fragments)
# et aux scores flottants finis dans une plage réaliste.
@st.composite
def _scored_lists(draw: st.DrawFn, *, min_size: int = 0, max_size: int = 40):
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    chunk_ids = draw(
        st.lists(
            st.integers(min_value=0, max_value=10_000),
            min_size=size,
            max_size=size,
            unique=True,
        )
    )
    scores = draw(
        st.lists(
            st.floats(
                min_value=-1_000.0,
                max_value=1_000.0,
                allow_nan=False,
                allow_infinity=False,
            ),
            min_size=size,
            max_size=size,
        )
    )
    return [_scored(cid, sc) for cid, sc in zip(chunk_ids, scores)]


def _sort_key(item: ScoredChunk) -> tuple[float, int]:
    """Clé de tri de référence : score décroissant, puis ``chunk_id`` croissant."""
    return (-item.score, item.chunk_id)


@settings(max_examples=200)
@given(scored=_scored_lists())
def test_rerank_returns_min_keep_len_items_all_from_input(
    scored: list[ScoredChunk],
) -> None:
    """``rerank`` renvoie ``min(keep, len)`` éléments, tous issus de l'entrée."""
    reranker = Reranker()

    result = reranker.rerank(scored)

    assert len(result) == min(DEFAULT_KEEP, len(scored))

    input_ids = [c.chunk_id for c in scored]
    result_ids = [c.chunk_id for c in result]
    # Chaque élément renvoyé provient de l'entrée, sans duplication.
    assert all(item in scored for item in result)
    assert len(result_ids) == len(set(result_ids))
    for cid in result_ids:
        assert cid in input_ids


@settings(max_examples=200)
@given(scored=_scored_lists())
def test_rerank_result_ordered_by_descending_score(
    scored: list[ScoredChunk],
) -> None:
    """Le résultat est ordonné par score décroissant (départage ``chunk_id``)."""
    reranker = Reranker()

    result = reranker.rerank(scored)

    scores = [item.score for item in result]
    assert scores == sorted(scores, reverse=True)
    # Ordre déterministe complet : score décroissant puis chunk_id croissant.
    keys = [_sort_key(item) for item in result]
    assert keys == sorted(keys)


@settings(max_examples=200)
@given(scored=_scored_lists(min_size=6, max_size=40))
def test_rerank_keeps_exactly_the_six_highest_scored(
    scored: list[ScoredChunk],
) -> None:
    """Pour >= 6 éléments, ``rerank`` retient exactement les 6 au score le plus élevé."""
    reranker = Reranker()

    result = reranker.rerank(scored)

    assert len(result) == DEFAULT_KEEP

    # Les 6 attendus = les 6 premiers du tri de référence sur l'entrée entière.
    expected = sorted(scored, key=_sort_key)[:DEFAULT_KEEP]
    assert result == expected
