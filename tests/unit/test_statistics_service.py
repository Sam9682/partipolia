"""Tests unitaires du ``StatisticsService`` (Exigence 22).

Couvre le calcul des compteurs globaux anonymisés :

* ``global_counts`` renvoie les six compteurs attendus — Utilisateurs,
  Propositions, Votes, Commentaires, Sources, Thèmes (Exigence 22.1) ;
* les compteurs sont de simples cardinalités agrégées, jamais ventilées par
  Utilisateur ni par valeur de Vote (Exigences 22.2, 22.3) ;
* les décomptes nuls (plateforme vide) produisent des zéros bornés à ``>= 0``.

La logique testée (un ``COUNT(*)`` par entité, assemblage du modèle Pydantic) est
celle du Service ; seule la couche d'accès aux données est remplacée par une
session factice qui renvoie un compte par table cible, afin d'exercer le vrai
code du Service sans base de données.
"""

from __future__ import annotations

import pytest

from app.services.statistics_service import StatisticsService

# Compteurs, dans l'ordre d'appel déterministe de ``global_counts`` :
# users, proposals, votes, comments, sources, themes.
_COUNTS: tuple[int, ...] = (7, 12, 40, 25, 5, 25)


class _FakeSession:
    """``AsyncSession`` factice : ``scalar`` renvoie un ``COUNT(*)`` par appel.

    Elle ne mocke pas la logique du Service. ``global_counts`` émet un
    ``SELECT count()`` par entité dans un ordre déterministe (users, proposals,
    votes, comments, sources, themes) ; la session renvoie successivement les
    comptes fournis, comme le feraient six vraies requêtes agrégées. Une valeur
    ``None`` (aucune ligne) est traitée par le Service comme ``0``.
    """

    def __init__(self, counts: tuple[int | None, ...]) -> None:
        self._counts = list(counts)
        self._calls = 0

    async def scalar(self, _statement: object) -> int | None:
        index = self._calls
        self._calls += 1
        if index < len(self._counts):
            return self._counts[index]
        return 0


def _service(counts: tuple[int | None, ...]) -> StatisticsService:
    return StatisticsService(_FakeSession(counts))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_global_counts_returns_all_six_counters() -> None:
    """``global_counts`` restitue les six compteurs attendus (Exigence 22.1)."""
    stats = await _service(_COUNTS).global_counts()

    assert stats.users == 7
    assert stats.proposals == 12
    assert stats.votes == 40
    assert stats.comments == 25
    assert stats.sources == 5
    assert stats.themes == 25


@pytest.mark.unit
async def test_empty_platform_yields_zero_counts() -> None:
    """Une plateforme vide (``COUNT`` nul/None) produit des compteurs nuls (Exigences 22.1, 22.2)."""
    stats = await _service((None, 0, None, 0, None, 0)).global_counts()

    assert stats.users == 0
    assert stats.proposals == 0
    assert stats.votes == 0
    assert stats.comments == 0
    assert stats.sources == 0
    assert stats.themes == 0


@pytest.mark.unit
async def test_counts_are_non_negative_and_serialisable() -> None:
    """Les compteurs sont bornés à >= 0 et sérialisables (agrégés, anonymisés)."""
    stats = await _service(_COUNTS).global_counts()
    dumped = stats.model_dump()

    assert set(dumped) == {"users", "proposals", "votes", "comments", "sources", "themes"}
    assert all(value >= 0 for value in dumped.values())
    # Les Votes sont un compteur global unique, sans ventilation par valeur ni par
    # Utilisateur (Exigence 22.3) : le modèle n'expose aucune clé individuelle.
    assert "user_id" not in dumped
    assert "value" not in dumped
