# Feature: partipolia-platform, Property 8: Somme des poids de fusion égale à 1.0
"""Test de propriété de la :class:`ScoreFusion` (Exigence 12.2).

**Property 8: Somme des poids de fusion égale à 1.0**

**Validates: Requirements 12.2**

Pour toute configuration valide de :class:`~app.rag.ranking.ScoreFusion`, la
somme des quatre poids de fusion vaut **exactement 1.0** (à la tolérance
flottante près). En conséquence, le score fusionné d'un candidat dont les
sous-scores appartiennent à ``[0, 1]`` est une **combinaison convexe** de ces
sous-scores, donc lui-même borné dans ``[0, 1]``. Tout jeu de poids dont la
somme s'écarte de 1.0 est **rejeté** à la construction (``ValueError``).

Le test exerce le vrai code de la fusion : les générateurs Hypothesis produisent
d'une part des poids valides sommant exactement à 1.0 (via le simplexe de
Dirichlet), d'autre part des sous-scores contraints à ``[0, 1]``, et enfin des
jeux de poids délibérément invalides pour vérifier le rejet.
"""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from app.rag.ranking import ScoreFusion
from app.rag.types import Candidate

# Composantes attendues par la fusion (ordre déterministe).
_COMPONENTS = ("semantic", "lexical", "source_quality", "recency")

# Tolérance alignée sur la validation interne de ScoreFusion.
_TOLERANCE = 1e-9


@st.composite
def _weights_summing_to_one(draw: st.DrawFn) -> dict[str, float]:
    """Poids valides couvrant les quatre composantes et sommant exactement à 1.0.

    On tire trois « coupures » dans ``[0, 1]`` puis on prend les écarts triés :
    cette construction (bâtons brisés) garantit une somme égale à 1.0 par
    construction algébrique, tout en couvrant largement le simplexe.
    """
    cuts = sorted(
        draw(
            st.lists(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
                min_size=3,
                max_size=3,
            )
        )
    )
    parts = [cuts[0], cuts[1] - cuts[0], cuts[2] - cuts[1], 1.0 - cuts[2]]
    return dict(zip(_COMPONENTS, parts))


# Sous-scores normalisés dans [0, 1] (comme le garantit le Retriever).
_sub_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False)


@pytest.mark.property
@pytest.mark.unit
@settings(max_examples=200)
@given(weights=_weights_summing_to_one())
def test_valid_weights_sum_to_one_and_fusion_is_bounded(
    weights: dict[str, float],
) -> None:
    """Poids valides ⇒ somme = 1.0 et score fusionné borné dans [0, 1]."""
    fusion = ScoreFusion(weights)

    # La somme des poids effectifs vaut exactement 1.0 (Property 8).
    assert sum(fusion.weights.values()) == pytest.approx(1.0, abs=_TOLERANCE)

    # Un candidat dont tous les sous-scores valent 1 obtient exactement 1.0
    # (les poids formant une partition de l'unité).
    saturated = Candidate(
        chunk_id=1,
        content="x",
        semantic=1.0,
        lexical=1.0,
        source_quality=1.0,
        recency=1.0,
    )
    assert fusion.score(saturated) == pytest.approx(1.0, abs=_TOLERANCE)


@pytest.mark.property
@pytest.mark.unit
@settings(max_examples=200)
@given(
    weights=_weights_summing_to_one(),
    semantic=_sub_score,
    lexical=_sub_score,
    source_quality=_sub_score,
    recency=_sub_score,
)
def test_fused_score_is_convex_combination_bounded_in_unit_interval(
    weights: dict[str, float],
    semantic: float,
    lexical: float,
    source_quality: float,
    recency: float,
) -> None:
    """Sous-scores ∈ [0, 1] + poids valides ⇒ score fusionné ∈ [0, 1]."""
    fusion = ScoreFusion(weights)
    candidate = Candidate(
        chunk_id=1,
        content="x",
        semantic=semantic,
        lexical=lexical,
        source_quality=source_quality,
        recency=recency,
    )

    score = fusion.score(candidate)

    # Combinaison convexe de valeurs de [0, 1] ⇒ résultat dans [0, 1].
    assert -_TOLERANCE <= score <= 1.0 + _TOLERANCE

    # Encadrement plus fin : entre le plus petit et le plus grand sous-score.
    subs = (semantic, lexical, source_quality, recency)
    assert min(subs) - _TOLERANCE <= score <= max(subs) + _TOLERANCE


@pytest.mark.property
@pytest.mark.unit
@settings(max_examples=200)
@given(
    weights=_weights_summing_to_one(),
    delta=st.floats(min_value=-0.5, max_value=0.5, allow_nan=False),
)
def test_weights_not_summing_to_one_are_rejected(
    weights: dict[str, float], delta: float
) -> None:
    """Un jeu de poids dont la somme s'écarte de 1.0 est rejeté (ValueError)."""
    # On ne considère que des perturbations réellement hors tolérance.
    assume(abs(delta) > _TOLERANCE * 10)

    perturbed = dict(weights)
    perturbed["semantic"] = perturbed["semantic"] + delta

    # La somme perturbée doit effectivement s'écarter de 1.0.
    assume(abs(sum(perturbed.values()) - 1.0) > _TOLERANCE)

    with pytest.raises(ValueError):
        ScoreFusion(perturbed)
