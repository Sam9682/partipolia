"""Tests unitaires du ``DuplicateDetectionService`` (Exigence 5 ; tâche 7.9).

Ces tests exercent le **vrai** code de rapprochement par similarité cosinus en ne
remplaçant que ce qui touche l'extérieur :

* l'``EmbeddingProvider`` est simulé (``_FakeEmbeddingProvider``) : il renvoie des
  vecteurs prédéfinis par texte, sans appel réseau (providers IA isolés —
  Exigence 16.4) ;
* la ``AsyncSession`` est simulée (``_FakeSession``) : sa méthode ``scalars``
  renvoie des Propositions candidates prédéfinies, sans base de données.

On vérifie que :

* les Propositions dont la similarité atteint le seuil sont retournées, triées
  par similarité décroissante et limitées à ``top_k`` (Exigence 5.1) ;
* les Propositions en dessous du seuil sont écartées ;
* un ``top_k`` non positif ou un texte vide court-circuite sans appel au
  fournisseur d'embeddings.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from app.services.duplicate_detection_service import (
    DuplicateDetectionService,
    _cosine_similarity,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeProposal:
    """Objet minimal exposant les attributs lus par le Service (id, slug, title, description)."""

    id: int
    slug: str
    title: str
    description: str
    status: str = "PUBLISHED"


class _ScalarsResult:
    def __init__(self, rows: list[_FakeProposal]) -> None:
        self._rows = rows

    def all(self) -> list[_FakeProposal]:
        return list(self._rows)


class _FakeSession:
    """``AsyncSession`` simulée : ``scalars`` renvoie des candidats fixes."""

    def __init__(self, rows: list[_FakeProposal]) -> None:
        self._rows = rows
        self.executed = 0

    async def scalars(self, _stmt: object) -> _ScalarsResult:
        self.executed += 1
        return _ScalarsResult(self._rows)


class _FakeEmbeddingProvider:
    """``EmbeddingProvider`` simulé : mappe chaque texte à un vecteur prédéfini."""

    def __init__(self, vectors_by_text: dict[str, list[float]]) -> None:
        self._vectors = vectors_by_text
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        # Repli sur un vecteur nul pour un texte inconnu (aucune proximité).
        return [self._vectors.get(text, [0.0, 0.0, 0.0]) for text in texts]


def _run(coro):
    return asyncio.run(coro)


def _text(title: str, description: str) -> str:
    """Reproduit la composition texte du Service (titre + description)."""
    return f"{title.strip()}\n\n{description.strip()}".strip()


def test_cosine_similarity_is_bounded_and_normalized() -> None:
    """La similarité cosinus est ramenée dans [0, 1] (1 = identique, 0.5 = orthogonal)."""
    assert _cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert _cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.5)
    assert _cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)
    # Vecteur nul ou dimensions incompatibles ⇒ 0.0.
    assert _cosine_similarity([0.0, 0.0], [1.0, 1.0]) == 0.0
    assert _cosine_similarity([1.0], [1.0, 0.0]) == 0.0


def test_find_similar_returns_proposals_above_threshold_sorted() -> None:
    """Les Propositions proches (≥ seuil) sont renvoyées, triées décroissant (Exigence 5.1)."""
    close = _FakeProposal(id=1, slug="proche", title="Gratuité des transports", description="Rendre les bus gratuits")
    far = _FakeProposal(id=2, slug="loin", title="Réforme fiscale", description="Baisser la TVA")

    # La requête et la Proposition « proche » partagent le même vecteur (similarité 1.0) ;
    # la Proposition « loin » est orthogonale (similarité 0.5 < seuil).
    query = _text("Transports gratuits", "Gratuité des bus pour tous")
    vectors = {
        query: [1.0, 0.0, 0.0],
        _text(close.title, close.description): [1.0, 0.0, 0.0],
        _text(far.title, far.description): [0.0, 1.0, 0.0],
    }
    session = _FakeSession([close, far])
    service = DuplicateDetectionService(session, _FakeEmbeddingProvider(vectors))

    result = _run(
        service.find_similar("Transports gratuits", "Gratuité des bus pour tous", threshold=0.82)
    )

    assert [item.id for item in result] == [1]
    assert result[0].slug == "proche"
    assert result[0].similarity == pytest.approx(1.0)


def test_find_similar_empty_when_none_above_threshold() -> None:
    """Aucune Proposition au-dessus du seuil ⇒ liste vide (pas de faux positif)."""
    far = _FakeProposal(id=2, slug="loin", title="Réforme fiscale", description="Baisser la TVA")
    query = _text("Transports gratuits", "Gratuité des bus")
    vectors = {
        query: [1.0, 0.0, 0.0],
        _text(far.title, far.description): [0.0, 1.0, 0.0],
    }
    session = _FakeSession([far])
    service = DuplicateDetectionService(session, _FakeEmbeddingProvider(vectors))

    result = _run(service.find_similar("Transports gratuits", "Gratuité des bus", threshold=0.82))

    assert result == []


def test_find_similar_respects_top_k() -> None:
    """Le nombre de résultats est limité à ``top_k`` (Exigence 5.1)."""
    rows = [
        _FakeProposal(id=i, slug=f"p{i}", title=f"Mesure {i}", description="Contenu proche")
        for i in range(1, 5)
    ]
    query = _text("Mesure", "Contenu proche")
    vectors = {query: [1.0, 0.0]}
    for row in rows:
        vectors[_text(row.title, row.description)] = [1.0, 0.0]
    session = _FakeSession(rows)
    service = DuplicateDetectionService(session, _FakeEmbeddingProvider(vectors))

    result = _run(service.find_similar("Mesure", "Contenu proche", threshold=0.5, top_k=2))

    assert len(result) == 2


def test_find_similar_short_circuits_without_provider_call() -> None:
    """top_k ≤ 0 ou texte vide ⇒ liste vide sans appel au fournisseur d'embeddings."""
    provider = _FakeEmbeddingProvider({})
    session = _FakeSession([])
    service = DuplicateDetectionService(session, provider)

    assert _run(service.find_similar("Titre", "Desc", top_k=0)) == []
    assert _run(service.find_similar("   ", "   ")) == []
    assert provider.calls == []


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
