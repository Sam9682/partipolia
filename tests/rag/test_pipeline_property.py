# Feature: partipolia-platform, Property 7: Ancrage documentaire des réponses RAG
"""Test de propriété de l'ancrage documentaire du ``RagPipeline`` (Exigences 13, 14).

**Property 7: Ancrage documentaire des réponses RAG**

**Validates: Requirements 13.5, 13.7, 13.8, 13.9, 14.1, 14.6**

Pour toute question et pour tout ensemble de passages simulés :

* la **récupération précède toujours la génération** (Exigence 14.1) : la
  recherche hybride est interrogée avant tout appel au LLM ;
* lorsque la récupération ne retient **aucun** passage, la réponse est
  « information insuffisante » (``confidence = 0.0``, ``sources = []``) et le LLM
  **n'est jamais appelé** — aucune invention de nombres (Exigences 13.8, 14.1,
  14.6, 13.9) ;
* lorsque des passages existent, les Sources de la réponse sont **entièrement
  tirées du contexte récupéré/reranké** (ancrage documentaire) : chaque Source
  correspond à un fragment fourni par la recherche (mêmes ``chunk_id``), sans
  fragment inventé, et la ``confidence`` ∈ ``[0, 1]`` reflète la validation des
  citations (Exigences 13.5, 13.7).

Les providers IA sont **simulés** (LLM enregistreur, recherche hybride factice) :
le test exerce le vrai code d'orchestration (classification, réécriture, fusion,
reranking, contexte, validation) hors-ligne, sans base ni réseau.
"""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.pipeline import (
    INSUFFICIENT_INFORMATION_MESSAGE,
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


class _OrderTrackingHybridSearch:
    """``HybridSearch`` simulée qui journalise l'ordre des appels (retrieval avant génération)."""

    def __init__(self, candidates: list[Candidate], events: list[str]) -> None:
        self._candidates = candidates
        self._events = events
        self.queries: list[str] = []

    async def search(self, query: str, *, top_k: int) -> list[Candidate]:
        self._events.append("search")
        self.queries.append(query)
        return list(self._candidates)


class _OrderTrackingLLM(_RecordingLLM):
    """LLM enregistreur qui journalise également l'ordre relatif des appels."""

    def __init__(self, response: str, events: list[str]) -> None:
        super().__init__(response)
        self._events = events

    def generate(self, messages: list[Message], temperature: float) -> str:
        self._events.append("generate")
        return super().generate(messages, temperature)


def _run(coro):
    """Exécute une coroutine dans une boucle dédiée (tests synchrones)."""
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# Générateurs Hypothesis                                                      #
# --------------------------------------------------------------------------- #

# Questions réalistes non vides : la réécriture doit produire une requête utile.
_questions = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x24F),
    min_size=1,
    max_size=80,
).filter(lambda s: s.strip() != "")

_sub_score = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)


@st.composite
def _candidates(draw: st.DrawFn, *, min_size: int, max_size: int) -> list[Candidate]:
    """Liste de candidats aux ``chunk_id`` uniques et sous-scores dans ``[0, 1]``."""
    size = draw(st.integers(min_value=min_size, max_value=max_size))
    chunk_ids = draw(
        st.lists(
            st.integers(min_value=1, max_value=10_000),
            min_size=size,
            max_size=size,
            unique=True,
        )
    )
    result: list[Candidate] = []
    for cid in chunk_ids:
        result.append(
            Candidate(
                chunk_id=cid,
                content=draw(st.text(min_size=1, max_size=60).filter(lambda s: s.strip() != "")),
                semantic=draw(_sub_score),
                lexical=draw(_sub_score),
                source_quality=draw(_sub_score),
                recency=draw(_sub_score),
                metadata={"source_id": 100 + cid, "document_id": 200 + cid},
            )
        )
    return result


# --------------------------------------------------------------------------- #
# Facette 1 — aucune récupération ⇒ information insuffisante, aucun LLM (14.1, 14.6, 13.8, 13.9) #
# --------------------------------------------------------------------------- #
@pytest.mark.property
@pytest.mark.rag
@settings(max_examples=200, deadline=None)
@given(message=_questions)
def test_no_passages_returns_insufficient_and_never_calls_llm(message: str) -> None:
    """Sans passage : « information insuffisante », confiance 0, sources [], LLM jamais appelé."""
    events: list[str] = []
    llm = _OrderTrackingLLM("Le montant est de 42 milliards d'euros.", events)
    search = _OrderTrackingHybridSearch(candidates=[], events=events)
    pipeline = RagPipeline(hybrid_search=search, llm_provider=llm, temperature=0.1)  # type: ignore[arg-type]

    response = _run(pipeline.answer(message, proposal_id=None))

    assert response.answer == INSUFFICIENT_INFORMATION_MESSAGE
    assert response.sources == []
    assert response.confidence == 0.0
    # Aucune génération : le LLM n'est jamais appelé (retrieval obligatoire, 14.1/14.6).
    assert llm.calls == []
    assert "generate" not in events
    # Aucun nombre inventé : la réponse est le message fixe d'indisponibilité (13.9).
    assert not any(ch.isdigit() for ch in response.answer)


# --------------------------------------------------------------------------- #
# Facette 2 — récupération avant génération (Exigence 14.1)                    #
# --------------------------------------------------------------------------- #
@pytest.mark.property
@pytest.mark.rag
@settings(max_examples=200, deadline=None)
@given(message=_questions, candidates=_candidates(min_size=1, max_size=12))
def test_retrieval_always_precedes_generation(
    message: str, candidates: list[Candidate]
) -> None:
    """La recherche hybride est toujours interrogée avant tout appel au LLM (14.1)."""
    events: list[str] = []
    llm = _OrderTrackingLLM("Réponse ancrée [1].", events)
    search = _OrderTrackingHybridSearch(candidates=candidates, events=events)
    pipeline = RagPipeline(hybrid_search=search, llm_provider=llm, temperature=0.1)  # type: ignore[arg-type]

    _run(pipeline.answer(message, proposal_id=None))

    # La recherche a bien eu lieu, et si le LLM a été appelé, c'est après elle.
    assert "search" in events
    if "generate" in events:
        assert events.index("search") < events.index("generate")


# --------------------------------------------------------------------------- #
# Facette 3 — ancrage documentaire : Sources tirées du contexte récupéré (13.5, 13.7) #
# --------------------------------------------------------------------------- #
@pytest.mark.property
@pytest.mark.rag
@settings(max_examples=200, deadline=None)
@given(message=_questions, candidates=_candidates(min_size=1, max_size=12))
def test_sources_are_grounded_in_retrieved_context(
    message: str, candidates: list[Candidate]
) -> None:
    """Chaque Source de la réponse provient d'un fragment récupéré (aucun inventé)."""
    llm = _RecordingLLM("Le budget prévu est documenté [1].")
    search = _OrderTrackingHybridSearch(candidates=candidates, events=[])
    pipeline = RagPipeline(hybrid_search=search, llm_provider=llm, temperature=0.1)  # type: ignore[arg-type]

    response = _run(pipeline.answer(message, proposal_id=None))

    retrieved_ids = {c.chunk_id for c in candidates}
    retrieved_contents = {c.chunk_id: c.content.strip() for c in candidates}

    # Ancrage : toute Source correspond à un fragment effectivement récupéré.
    for source in response.sources:
        assert source.chunk_id in retrieved_ids, "Source hors du contexte récupéré (inventée)"
        assert source.content == retrieved_contents[source.chunk_id]

    # Numérotation des citations : croissante à partir de 1, sans trou (Exigence 13.4).
    numbers = [source.number for source in response.sources]
    assert numbers == list(range(1, len(numbers) + 1))

    # Au plus 6 Sources retenues (reranking), toutes distinctes (pas de duplication).
    assert len(response.sources) <= 6
    source_ids = [source.chunk_id for source in response.sources]
    assert len(source_ids) == len(set(source_ids))

    # La confiance reste bornée dans [0, 1] et reflète la validation des citations.
    assert 0.0 <= response.confidence <= 1.0


# --------------------------------------------------------------------------- #
# Facette 4 — la confiance reflète la validation des citations (13.7)         #
# --------------------------------------------------------------------------- #
@pytest.mark.property
@pytest.mark.rag
@settings(max_examples=200, deadline=None)
@given(message=_questions, candidates=_candidates(min_size=1, max_size=12))
def test_fabricated_citation_number_lowers_confidence(
    message: str, candidates: list[Candidate]
) -> None:
    """Une réponse citant un numéro fabriqué (hors contexte) n'a pas la confiance maximale (13.7)."""
    # [999] ne peut jamais correspondre à une citation fournie (au plus 6 retenues).
    llm = _RecordingLLM("Le taux atteint 12 % selon la source [999].")
    search = _OrderTrackingHybridSearch(candidates=candidates, events=[])
    pipeline = RagPipeline(hybrid_search=search, llm_provider=llm, temperature=0.1)  # type: ignore[arg-type]

    response = _run(pipeline.answer(message, proposal_id=None))

    # Des Sources ancrées existent, mais l'affirmation chiffrée cite un numéro
    # inexistant : la validation dégrade la confiance sous le maximum.
    assert response.sources, "des passages étaient disponibles : des Sources sont attendues"
    assert response.confidence < 0.9


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
