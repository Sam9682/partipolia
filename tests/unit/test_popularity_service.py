"""Tests unitaires du ``PopularityService`` (Exigence 7).

Couvre le calcul des décomptes absolus et du Taux_De_Soutien :

* ``support_count`` / ``oppose_count`` / ``participation_count`` (Exigence 7.1) ;
* ``support_rate = support / (support + oppose)`` avec ``0`` si le dénominateur
  est nul (Exigence 7.2) ;
* décomptes absolus toujours renvoyés aux côtés du taux (Exigence 7.4) ;
* Votes NEUTRE (``value == 0``) exclus du dénominateur mais comptés dans la
  participation.

La logique testée (agrégation des lignes groupées par ``value`` puis calcul du
taux) est celle du Service ; seule la couche d'accès aux données est remplacée
par une session factice renvoyant des lignes ``(value, count)`` prédéfinies, afin
d'exercer le vrai code du Service sans base de données.
"""

from __future__ import annotations

import pytest

from app.services.popularity_service import PopularityService


class _FakeResult:
    """Résultat d'exécution factice exposant ``all()`` (lignes ``(value, count)``)."""

    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self._rows = rows

    def all(self) -> list[tuple[int, int]]:
        return self._rows


class _FakeSession:
    """``AsyncSession`` factice : ``execute`` renvoie des lignes prédéfinies.

    Elle ne mocke pas la logique du Service ; elle ne fait que fournir le jeu de
    lignes agrégées qu'une vraie requête ``GROUP BY value`` produirait.
    """

    def __init__(self, rows: list[tuple[int, int]]) -> None:
        self._rows = rows

    async def execute(self, _statement: object) -> _FakeResult:
        return _FakeResult(self._rows)


def _service(rows: list[tuple[int, int]]) -> PopularityService:
    return PopularityService(_FakeSession(rows))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_no_votes_yields_zero_counts_and_zero_rate() -> None:
    """Sans Vote, tous les décomptes sont nuls et le taux vaut 0 (Exigence 7.2)."""
    popularity = await _service([]).compute(42)

    assert popularity.proposal_id == 42
    assert popularity.support_count == 0
    assert popularity.oppose_count == 0
    assert popularity.participation_count == 0
    assert popularity.support_rate == 0.0


@pytest.mark.unit
async def test_mixed_votes_counts_and_rate() -> None:
    """3 soutiens, 1 opposition, 2 NEUTRE ⇒ taux 0.75 et participation 6 (Exigences 7.1, 7.2)."""
    popularity = await _service([(1, 3), (-1, 1), (0, 2)]).compute(1)

    assert popularity.support_count == 3
    assert popularity.oppose_count == 1
    assert popularity.participation_count == 6
    assert popularity.support_rate == pytest.approx(0.75)


@pytest.mark.unit
async def test_neutral_votes_excluded_from_rate_denominator() -> None:
    """Le NEUTRE compte dans la participation mais pas dans le dénominateur (Exigence 7.2)."""
    # 2 soutiens, 0 opposition, 10 NEUTRE : dénominateur = 2 ⇒ taux = 1.0.
    popularity = await _service([(1, 2), (0, 10)]).compute(7)

    assert popularity.support_count == 2
    assert popularity.oppose_count == 0
    assert popularity.participation_count == 12
    assert popularity.support_rate == 1.0


@pytest.mark.unit
async def test_only_neutral_votes_gives_zero_rate_with_positive_participation() -> None:
    """Uniquement des NEUTRE ⇒ dénominateur nul ⇒ taux 0, participation non nulle (Exigence 7.2)."""
    popularity = await _service([(0, 5)]).compute(3)

    assert popularity.support_count == 0
    assert popularity.oppose_count == 0
    assert popularity.participation_count == 5
    assert popularity.support_rate == 0.0


@pytest.mark.unit
async def test_all_opposition_gives_zero_rate() -> None:
    """Uniquement des oppositions ⇒ taux 0 avec décomptes absolus renvoyés (Exigences 7.2, 7.4)."""
    popularity = await _service([(-1, 4)]).compute(9)

    assert popularity.support_count == 0
    assert popularity.oppose_count == 4
    assert popularity.participation_count == 4
    assert popularity.support_rate == 0.0


@pytest.mark.unit
async def test_all_support_gives_full_rate() -> None:
    """Uniquement des soutiens ⇒ taux 1.0 (Exigence 7.2)."""
    popularity = await _service([(1, 4)]).compute(11)

    assert popularity.support_count == 4
    assert popularity.oppose_count == 0
    assert popularity.participation_count == 4
    assert popularity.support_rate == 1.0


@pytest.mark.unit
async def test_rate_is_always_within_unit_interval() -> None:
    """Le taux reste borné à [0, 1] quels que soient les décomptes (Exigence 7.2)."""
    popularity = await _service([(1, 7), (-1, 13), (0, 5)]).compute(2)

    assert 0.0 <= popularity.support_rate <= 1.0
    assert popularity.support_rate == pytest.approx(7 / 20)
    assert popularity.participation_count == 25
