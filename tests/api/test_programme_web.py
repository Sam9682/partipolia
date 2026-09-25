"""Tests de la page Web SSR du Programme ``/programme`` (Exigences 24.2, 24.3, 24.4, 25.1 ; tâche 8.5).

Ces tests exercent la couche Web de ``app/web/router.py`` (rendu Jinja2, câblage
de la route, consultation publique) sans base de données : le
:class:`~app.services.program_service.ProgramService` est remplacé par une
implémentation factice renvoyant une :class:`ProgramPageView` en mémoire. La
vraie logique d'agrégation du Service est couverte par
``tests/unit/test_program_service.py`` ; on valide ici uniquement le rendu de la
page et son accessibilité publique.

Points vérifiés :

* ``GET /programme`` répond ``200`` **sans authentification** (Exigence 24.4) ;
* la page liste les Thèmes et, par mesure, le nombre de propositions, le nombre
  de votes, le soutien agrégé et les coûts/recettes/économies estimés
  (Exigence 24.2) ;
* les hypothèses des agrégats financiers sont affichées (Exigence 24.3) ;
* les montants non estimés (``None``) sont restitués sans agrégat inventé.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.schemas.program import (
    ProgramMeasure,
    ProgramPageView,
    ProgramThemeGroup,
)
from app.web import router as web

pytestmark = pytest.mark.api


class _FakeProgramService:
    """``ProgramService`` factice renvoyant une :class:`ProgramPageView` fixe."""

    def __init__(self, view: ProgramPageView) -> None:
        self._view = view

    async def build_public_program_view(self) -> ProgramPageView:
        return self._view


def _sample_view() -> ProgramPageView:
    """Vue Programme d'exemple : un Thème, deux mesures (dont une non estimée)."""
    measures = [
        ProgramMeasure(
            proposal_id=1,
            slug="transport-gratuit",
            title="Transport public gratuit",
            support_count=8,
            oppose_count=2,
            participation_count=11,
            support_rate=0.8,
            estimated_cost=1_200_000.0,
            estimated_revenue=None,
            estimated_savings=50_000.0,
        ),
        ProgramMeasure(
            proposal_id=2,
            slug="pistes-cyclables",
            title="Extension des pistes cyclables",
            support_count=3,
            oppose_count=3,
            participation_count=6,
            support_rate=0.5,
            estimated_cost=None,
            estimated_revenue=None,
            estimated_savings=None,
        ),
    ]
    theme = ProgramThemeGroup(
        theme_id=10,
        slug="mobilite",
        name="Mobilité",
        measure_count=2,
        proposal_count=2,
        vote_count=17,
        support_count=11,
        oppose_count=5,
        support_rate=11 / 16,
        estimated_cost=1_200_000.0,
        estimated_revenue=None,
        estimated_savings=50_000.0,
        measures=measures,
    )
    return ProgramPageView(
        program_id=1,
        themes=[theme],
        total_measure_count=2,
        total_vote_count=17,
        total_estimated_cost=1_200_000.0,
        total_estimated_revenue=None,
        total_estimated_savings=50_000.0,
    )


def _build_app(service: _FakeProgramService) -> FastAPI:
    """Assemble une application ne montant que le routeur Web (aucune auth)."""
    app = FastAPI()
    app.include_router(web.web_router)
    app.dependency_overrides[web.get_program_service] = lambda: service
    return app


def _client(view: ProgramPageView) -> TestClient:
    return TestClient(_build_app(_FakeProgramService(view)))


def test_root_redirects_to_programme() -> None:
    """``GET /`` redirige (307) vers la page publique ``/programme`` (plus de 404)."""
    response = _client(_sample_view()).get("/", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == "/programme"


def test_root_follows_redirect_to_programme_ok() -> None:
    """En suivant la redirection, ``GET /`` aboutit à la page Programme (``200``)."""
    response = _client(_sample_view()).get("/")

    assert response.status_code == 200
    assert "Programme" in response.text


def test_programme_is_public_and_renders() -> None:
    """``GET /programme`` est public (``200``) et rend du HTML (Exigences 24.4, 25.1)."""
    response = _client(_sample_view()).get("/programme")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Programme" in response.text


def test_programme_lists_theme_and_measures() -> None:
    """La page liste les Thèmes et leurs mesures (Exigence 24.2)."""
    body = _client(_sample_view()).get("/programme").text

    assert "Mobilité" in body
    assert "Transport public gratuit" in body
    assert "Extension des pistes cyclables" in body
    # Lien vers la fiche Proposition.
    assert "/propositions/transport-gratuit" in body


def test_programme_shows_counts_and_support() -> None:
    """Nombre de votes et soutien agrégé (décomptes absolus + taux) sont affichés (Exigences 24.2, 7.4)."""
    body = _client(_sample_view()).get("/programme").text

    # Total des votes exprimés.
    assert "17" in body
    # Décomptes absolus du soutien affichés à côté du pourcentage (jamais seul).
    assert "8 pour / 2 contre" in body
    assert "80.0%" in body


def test_programme_shows_financial_aggregates_and_assumptions() -> None:
    """Agrégats financiers estimés et leurs hypothèses sont affichés (Exigences 24.2, 24.3)."""
    body = _client(_sample_view()).get("/programme").text

    assert 'data-testid="financial-assumptions"' in body
    assert "Hypothèses des agrégats financiers" in body
    # Montant estimé formaté (espace comme séparateur de milliers).
    assert "1 200 000 €" in body
    # Montant non estimé restitué sans agrégat inventé.
    assert "Non estimé" in body


def test_programme_empty_when_no_measures() -> None:
    """Une vue sans mesure affiche l'état vide, sans erreur (Exigence 24.4)."""
    empty = ProgramPageView(
        program_id=1,
        themes=[],
        total_measure_count=0,
        total_vote_count=0,
    )
    response = _client(empty).get("/programme")

    assert response.status_code == 200
    assert 'data-testid="empty-program"' in response.text
