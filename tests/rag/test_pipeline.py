"""Tests unitaires du ``RagPipeline`` (Exigences 13, 14, 15).

Ces tests exercent le **vrai** code d'orchestration du pipeline (classification,
réécriture, fusion, reranking, construction de contexte, validation) en ne
remplaçant que ce qui touche l'extérieur :

* le ``LLMProvider`` est simulé (``_RecordingLLM``) : il n'appelle aucun service
  externe et enregistre les messages/la température reçus afin de vérifier le
  prompt système et la faible température (Exigences 14.2, 13.5–13.10, 15) ;
* la ``HybridSearch`` est simulée (``_FakeHybridSearch``) : elle renvoie des
  :class:`~app.rag.types.Candidate` prédéfinis sans base de données, ce qui
  permet de tester l'ancrage documentaire et la règle « retrieval obligatoire »
  (Exigences 14.1, 14.6, 13.8).

Aucune écriture n'est possible via ces doublures : le pipeline reste en lecture
seule côté domaine (Property 10).
"""

from __future__ import annotations

import asyncio

import pytest

from app.rag.pipeline import (
    INSUFFICIENT_INFORMATION_MESSAGE,
    SYSTEM_PROMPT,
    RagPipeline,
)
from app.rag.providers.base import Message
from app.rag.types import Candidate


class _RecordingLLM:
    """``LLMProvider`` simulé : renvoie une réponse fixe et enregistre ses appels."""

    def __init__(self, response: str) -> None:
        self._response = response
        self.calls: list[tuple[list[Message], float]] = []

    def generate(self, messages: list[Message], temperature: float) -> str:
        self.calls.append((messages, temperature))
        return self._response


class _FakeHybridSearch:
    """``HybridSearch`` simulée : renvoie des candidats prédéfinis, sans base."""

    def __init__(self, candidates: list[Candidate]) -> None:
        self._candidates = candidates
        self.queries: list[str] = []

    async def search(self, query: str, *, top_k: int) -> list[Candidate]:
        self.queries.append(query)
        return list(self._candidates)


def _candidate(chunk_id: int, content: str, *, semantic: float = 0.9) -> Candidate:
    """Construit un ``Candidate`` de test avec des métadonnées minimales."""
    return Candidate(
        chunk_id=chunk_id,
        content=content,
        semantic=semantic,
        lexical=0.5,
        source_quality=0.8,
        recency=0.5,
        metadata={"source_id": 100 + chunk_id, "document_id": 200 + chunk_id},
    )


def _build_pipeline(
    llm: _RecordingLLM, candidates: list[Candidate]
) -> tuple[RagPipeline, _FakeHybridSearch]:
    """Assemble un pipeline avec doublures LLM/recherche (autres briques réelles)."""
    search = _FakeHybridSearch(candidates)
    pipeline = RagPipeline(
        hybrid_search=search,  # type: ignore[arg-type]
        llm_provider=llm,
        temperature=0.1,
    )
    return pipeline, search


def _run(coro):
    """Exécute une coroutine dans une boucle dédiée (tests synchrones)."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Retrieval obligatoire et « information insuffisante » (Exigences 14.1, 14.6) #
# --------------------------------------------------------------------------- #
def test_no_passages_returns_insufficient_without_generation() -> None:
    """Sans passage récupéré : « information insuffisante », aucune génération (14.1, 14.6)."""
    llm = _RecordingLLM("ne devrait pas être appelé")
    pipeline, _ = _build_pipeline(llm, candidates=[])

    response = _run(pipeline.answer("Quel est le coût de la mesure ?", proposal_id=None))

    assert response.answer == INSUFFICIENT_INFORMATION_MESSAGE
    assert response.sources == []
    assert response.confidence == 0.0
    # Retrieval obligatoire AVANT génération : le LLM n'est jamais appelé.
    assert llm.calls == []


def test_empty_query_short_circuits_without_search_or_generation() -> None:
    """Une requête vide après réécriture n'appelle ni recherche ni génération."""
    llm = _RecordingLLM("x")
    pipeline, search = _build_pipeline(llm, candidates=[_candidate(1, "texte")])

    response = _run(pipeline.answer("   ", proposal_id=None))

    assert response.answer == INSUFFICIENT_INFORMATION_MESSAGE
    assert response.confidence == 0.0
    assert search.queries == []
    assert llm.calls == []


# --------------------------------------------------------------------------- #
# Réponse ancrée + faible température + prompt système (13.3–13.10, 14.2, 15)  #
# --------------------------------------------------------------------------- #
def test_answer_returns_sources_and_uses_low_temperature() -> None:
    """Avec des passages : réponse citée, Sources exposées, faible température (13.4, 14.2)."""
    llm = _RecordingLLM("Le budget est de 3 milliards d'euros [1].")
    candidates = [_candidate(1, "Le budget prévu est de 3 milliards d'euros.")]
    pipeline, _ = _build_pipeline(llm, candidates)

    response = _run(pipeline.answer("Quel est le budget ?", proposal_id=None))

    assert response.answer == "Le budget est de 3 milliards d'euros [1]."
    assert len(response.sources) == 1
    assert response.sources[0].number == 1
    assert response.sources[0].chunk_id == 1
    assert response.sources[0].source_id == 101
    assert response.confidence > 0.0

    # Faible température (Exigence 14.2) et prompt système présent (Exigence 13.5+).
    messages, temperature = llm.calls[0]
    assert temperature <= 0.3
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == SYSTEM_PROMPT or SYSTEM_PROMPT in messages[0]["content"]
    # Le contexte documentaire numéroté est transmis au LLM (Exigence 13.4).
    assert "[1]" in messages[1]["content"]


def test_temperature_is_capped_low_even_if_configured_high() -> None:
    """La température est plafonnée « faible » quelle que soit la config (Exigence 14.2)."""
    llm = _RecordingLLM("Réponse [1].")
    search = _FakeHybridSearch([_candidate(1, "passage")])
    pipeline = RagPipeline(
        hybrid_search=search,  # type: ignore[arg-type]
        llm_provider=llm,
        temperature=1.9,
    )

    _run(pipeline.answer("Question ?", proposal_id=None))

    _, temperature = llm.calls[0]
    assert temperature <= 0.3


def test_system_prompt_contains_guardrails() -> None:
    """Le prompt système matérialise les garde-fous (Exigences 13.5–13.10, 15)."""
    normalized = SYSTEM_PROMPT.lower()
    # Répondre uniquement à partir du contexte (13.5).
    assert "uniquement" in normalized or "exclusivement" in normalized
    # Ne pas inventer de nombres (13.9).
    assert "invent" in normalized
    # Distinguer faits / estimations / opinions / hypothèses / désaccords (13.6).
    for term in ("fait", "estimation", "opinion", "hypoth", "désaccord"):
        assert term in normalized
    # Pas de recommandation de vote individuelle (15.3).
    assert "vote" in normalized
    # Comparaison sur critères factuels (15.2).
    assert "coût" in normalized and "calendrier" in normalized


# --------------------------------------------------------------------------- #
# Validation des citations → confiance (Exigences 14.3–14.5)                   #
# --------------------------------------------------------------------------- #
def test_unsourced_factual_claim_lowers_confidence() -> None:
    """Une affirmation factuelle non sourcée fait baisser la confiance (14.5)."""
    # Réponse chiffrée SANS marqueur de citation valide.
    llm = _RecordingLLM("Le coût atteint 5 millions d'euros par an.")
    pipeline, _ = _build_pipeline(llm, [_candidate(1, "passage sur le coût")])

    response = _run(pipeline.answer("Quel coût ?", proposal_id=None))

    # Réponse produite mais confiance dégradée (affirmation factuelle non sourcée).
    assert response.confidence < 0.9


def test_fabricated_citation_lowers_confidence() -> None:
    """Une citation fabriquée (numéro absent) dégrade la confiance (14.4)."""
    # Un seul passage (donc [1] valide) mais la réponse cite [9] inexistant.
    llm = _RecordingLLM("Selon la source, le taux est de 12 % [9].")
    pipeline, _ = _build_pipeline(llm, [_candidate(1, "passage")])

    response = _run(pipeline.answer("Quel taux ?", proposal_id=None))

    assert response.confidence < 0.9


def test_fully_sourced_answer_has_high_confidence() -> None:
    """Une réponse entièrement sourcée obtient une confiance élevée (14.3)."""
    llm = _RecordingLLM("Le budget est de 3 milliards d'euros [1].")
    pipeline, _ = _build_pipeline(llm, [_candidate(1, "budget de 3 milliards")])

    response = _run(pipeline.answer("Budget ?", proposal_id=None))

    assert response.confidence >= 0.9


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
