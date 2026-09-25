# Feature: partipolia-platform, Property 3: Taux de soutien borné et exclusion du NEUTRE
"""Test de propriété du ``PopularityService`` (Exigence 7).

**Property 3: Taux de soutien borné et exclusion du NEUTRE**

**Validates: Requirements 7.2**

Pour tout multiensemble de valeurs de Votes ∈ {+1, 0, -1} associées à une
Proposition, le Taux_De_Soutien vaut ``support_count / (support_count +
oppose_count)``, appartient toujours à l'intervalle ``[0, 1]``, vaut exactement
``0`` lorsque le dénominateur est nul, et reste **inchangé** par l'ajout ou le
retrait de Votes NEUTRE (``0``), qui sont exclus du dénominateur.

Le test exerce le vrai code du Service : seule la couche d'accès aux données est
remplacée par une session factice renvoyant les lignes ``(value, count)`` qu'une
requête ``GROUP BY value`` produirait. Les générateurs Hypothesis contraignent
intelligemment l'espace d'entrée à des décomptes non négatifs de soutiens,
d'oppositions et de NEUTRE.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.popularity_service import PopularityService


class _FakeResult:
    """Résultat d'exécution factice exposant ``all()`` (lignes ``(value, count)``)."""

    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, int]]:
        return self._rows


class _FakeSession:
    """``AsyncSession`` factice : ``execute`` renvoie des lignes agrégées prédéfinies."""

    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self._rows = rows

    async def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _rows(support: int, oppose: int, neutral: int) -> list[tuple[int, int]]:
    """Construit les lignes ``(value, count)`` en n'incluant que les valeurs présentes.

    Une vraie requête ``GROUP BY value`` n'émet pas de ligne pour un ``value``
    absent ; on reproduit fidèlement cette forme.
    """
    rows: list[tuple[int, int]] = []
    if support:
        rows.append((1, support))
    if oppose:
        rows.append((-1, oppose))
    if neutral:
        rows.append((0, neutral))
    return rows


def _service(rows: list[tuple[int, int]]) -> PopularityService:
    return PopularityService(_FakeSession(rows))  # type: ignore[arg-type]


# Décomptes non négatifs, bornés pour garder les exemples lisibles sans réduire
# la couverture de l'espace pertinent (soutiens/oppositions/NEUTRE).
_counts = st.integers(min_value=0, max_value=1000)


@pytest.mark.property
@settings(max_examples=200)
@given(support=_counts, oppose=_counts, neutral=_counts)
async def test_support_rate_is_bounded_and_matches_definition(
    support: int, oppose: int, neutral: int
) -> None:
    """Le taux est dans [0, 1], vaut 0 si dénominateur nul, et suit sa définition."""
    popularity = await _service(_rows(support, oppose, neutral)).compute(1)

    denominator = support + oppose

    # Décomptes fidèlement restitués.
    assert popularity.support_count == support
    assert popularity.oppose_count == oppose
    assert popularity.participation_count == support + oppose + neutral

    # Borné à l'intervalle unité.
    assert 0.0 <= popularity.support_rate <= 1.0

    if denominator == 0:
        # Dénominateur nul ⇒ taux exactement 0 (Exigence 7.2).
        assert popularity.support_rate == 0.0
    else:
        # Conforme à la définition support / (support + oppose).
        assert popularity.support_rate == pytest.approx(support / denominator)


@pytest.mark.property
@settings(max_examples=200)
@given(support=_counts, oppose=_counts, neutral=_counts, extra_neutral=_counts)
async def test_neutral_votes_do_not_affect_support_rate(
    support: int, oppose: int, neutral: int, extra_neutral: int
) -> None:
    """Ajouter/retirer des NEUTRE ne change pas le taux (exclusion du dénominateur)."""
    base = await _service(_rows(support, oppose, neutral)).compute(1)
    with_more_neutral = await _service(
        _rows(support, oppose, neutral + extra_neutral)
    ).compute(1)

    # Le taux est inchangé par l'ajout de NEUTRE...
    assert with_more_neutral.support_rate == base.support_rate
    # ...alors même que la participation, elle, augmente d'autant.
    assert (
        with_more_neutral.participation_count
        == base.participation_count + extra_neutral
    )
