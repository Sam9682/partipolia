"""Tests unitaires des briques de compréhension/citation du pipeline RAG.

Couvre les quatre composants de la tâche 7.5 (Exigences 13.2, 13.3, 13.4, 14.3,
14.4, 14.5) :

* :class:`QuestionClassifier` — classification en
  GENERAL/ON_PROPOSITION/DOCUMENTARY/COMPARATIVE (Exigence 13.2) ;
* :class:`QueryRewriter` — réécriture/contextualisation de la requête (13.3) ;
* :class:`ContextBuilder` — contexte avec citations numérotées (13.4) ;
* :class:`CitationValidator` — détection des affirmations factuelles non
  sourcées (14.3, 14.4, 14.5).

La logique testée est pure (aucun accès base ni appel LLM) ; une Proposition
factice légère suffit pour exercer la réécriture ``ON_PROPOSITION``.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.rag.classifier import QuestionClassifier, QuestionType
from app.rag.context import CitationValidator, ContextBuilder
from app.rag.rewriter import QueryRewriter
from app.rag.types import Candidate, Citation, ScoredChunk


@dataclass
class _FakeProposal:
    """Proposition factice minimale exposant ``title`` et ``problem``."""

    title: str
    problem: str = ""


def _scored(chunk_id: int, content: str, **metadata: object) -> ScoredChunk:
    """Construit un ``ScoredChunk`` de test avec métadonnées optionnelles."""
    candidate = Candidate(
        chunk_id=chunk_id,
        content=content,
        semantic=1.0,
        metadata=metadata or None,
    )
    return ScoredChunk(candidate=candidate, score=1.0)


# --------------------------------------------------------------------------- #
# QuestionClassifier — Exigence 13.2                                          #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_classifier_general_without_proposal_or_markers() -> None:
    """Une question neutre sans proposition est GENERAL (Exigence 13.2)."""
    result = QuestionClassifier().classify("Bonjour, qui es-tu ?", None)
    assert result is QuestionType.GENERAL


@pytest.mark.unit
def test_classifier_on_proposition_when_proposal_id_given() -> None:
    """Un ``proposal_id`` ancre la question sur une Proposition (Exigence 13.2)."""
    result = QuestionClassifier().classify("Peux-tu m'expliquer ?", 12)
    assert result is QuestionType.ON_PROPOSITION


@pytest.mark.unit
def test_classifier_on_proposition_via_marker_without_id() -> None:
    """« cette mesure » sans id est aussi ON_PROPOSITION (Exigence 13.2)."""
    result = QuestionClassifier().classify("Que change cette mesure ?", None)
    assert result is QuestionType.ON_PROPOSITION


@pytest.mark.unit
def test_classifier_documentary_via_source_terms() -> None:
    """Une demande d'éléments sourcés est DOCUMENTARY (Exigence 13.2)."""
    result = QuestionClassifier().classify(
        "Que disent les sources sur le coût estimé ?", None
    )
    assert result is QuestionType.DOCUMENTARY


@pytest.mark.unit
def test_classifier_documentary_is_accent_insensitive() -> None:
    """La détection ignore les accents (« étude » ≈ « etude »)."""
    result = QuestionClassifier().classify("Quelle etude appuie ce point ?", None)
    assert result is QuestionType.DOCUMENTARY


@pytest.mark.unit
def test_classifier_comparative_takes_priority_over_proposition() -> None:
    """Une intention comparative prime, même avec un ``proposal_id`` (Exigence 13.2)."""
    result = QuestionClassifier().classify(
        "Peux-tu comparer cette mesure à l'alternative ?", 5
    )
    assert result is QuestionType.COMPARATIVE


@pytest.mark.unit
def test_classifier_empty_message_defaults() -> None:
    """Message vide : ON_PROPOSITION si id, sinon GENERAL (Exigence 13.2)."""
    classifier = QuestionClassifier()
    assert classifier.classify("   ", None) is QuestionType.GENERAL
    assert classifier.classify("", 3) is QuestionType.ON_PROPOSITION


# --------------------------------------------------------------------------- #
# QueryRewriter — Exigence 13.3                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_rewriter_general_collapses_whitespace() -> None:
    """Pour GENERAL, le message est renvoyé normalisé (Exigence 13.3)."""
    rewritten = QueryRewriter().rewrite(
        "  quel   est  le  budget ?  ", QuestionType.GENERAL, None
    )
    assert rewritten == "quel est le budget ?"


@pytest.mark.unit
def test_rewriter_on_proposition_prepends_context() -> None:
    """Pour ON_PROPOSITION, titre et problème ancrent la requête (Exigence 13.3)."""
    proposal = _FakeProposal(
        title="Gratuité des transports", problem="Congestion urbaine"
    )
    rewritten = QueryRewriter().rewrite(
        "Quel est son coût ?", QuestionType.ON_PROPOSITION, proposal  # type: ignore[arg-type]
    )
    assert "Gratuité des transports" in rewritten
    assert "Congestion urbaine" in rewritten
    assert rewritten.endswith("Quel est son coût ?")


@pytest.mark.unit
def test_rewriter_on_proposition_without_proposal_falls_back_to_message() -> None:
    """Sans Proposition, ON_PROPOSITION renvoie le message normalisé (Exigence 13.3)."""
    rewritten = QueryRewriter().rewrite(
        "Quel impact ?", QuestionType.ON_PROPOSITION, None
    )
    assert rewritten == "Quel impact ?"


@pytest.mark.unit
def test_rewriter_documentary_keeps_message_terms() -> None:
    """Pour DOCUMENTARY, les termes du message sont conservés (Exigence 13.3)."""
    rewritten = QueryRewriter().rewrite(
        "coût estimé de la réforme", QuestionType.DOCUMENTARY, None
    )
    assert rewritten == "coût estimé de la réforme"


# --------------------------------------------------------------------------- #
# ContextBuilder — Exigence 13.4                                              #
# --------------------------------------------------------------------------- #
@pytest.mark.unit
def test_context_builder_numbers_citations_sequentially() -> None:
    """Les citations sont numérotées 1..n sans trou, dans l'ordre (Exigence 13.4)."""
    chunks = [
        _scored(101, "Le coût est de 2 milliards.", source_id=7, document_id=3),
        _scored(102, "Le délai est de 3 ans.", source_id=8, document_id=4),
    ]
    context, citations = ContextBuilder().build(chunks)

    assert [c.number for c in citations] == [1, 2]
    assert citations[0].chunk_id == 101
    assert citations[0].source_id == 7
    assert citations[0].document_id == 3
    assert "[1] Le coût est de 2 milliards." in context
    assert "[2] Le délai est de 3 ans." in context


@pytest.mark.unit
def test_context_builder_empty_input() -> None:
    """Sans fragment, le contexte est vide et aucune citation n'est émise (Exigence 13.4)."""
    context, citations = ContextBuilder().build([])
    assert context == ""
    assert citations == []


# --------------------------------------------------------------------------- #
# CitationValidator — Exigences 14.3, 14.4, 14.5                              #
# --------------------------------------------------------------------------- #
def _citations(*numbers: int) -> list[Citation]:
    """Construit une liste de citations valides aux ``numbers`` fournis."""
    return [
        Citation(number=n, chunk_id=100 + n, content=f"passage {n}") for n in numbers
    ]


@pytest.mark.unit
def test_validator_accepts_sourced_factual_claim() -> None:
    """Une affirmation factuelle citée est valide (Exigences 14.3, 14.4)."""
    result = CitationValidator().validate(
        "Le coût est de 2 milliards d'euros [1].", _citations(1)
    )
    assert result.is_valid
    assert result.unsourced_claims == ()
    assert result.cited_numbers == (1,)


@pytest.mark.unit
def test_validator_detects_unsourced_factual_claim() -> None:
    """Une affirmation chiffrée sans citation est détectée non sourcée (Exigence 14.5)."""
    result = CitationValidator().validate(
        "Le coût est de 2 milliards d'euros.", _citations(1)
    )
    assert not result.is_valid
    assert len(result.unsourced_claims) == 1
    assert "2 milliards" in result.unsourced_claims[0]


@pytest.mark.unit
def test_validator_ignores_non_factual_sentences() -> None:
    """Une phrase d'opinion sans chiffre n'exige pas de citation (Exigence 14.5)."""
    result = CitationValidator().validate(
        "Voici les arguments documentés sur ce sujet.", _citations(1)
    )
    assert result.is_valid
    assert result.unsourced_claims == ()


@pytest.mark.unit
def test_validator_insufficient_information_is_not_penalized() -> None:
    """« information insuffisante » n'est jamais une affirmation non sourcée (Exigence 14.6)."""
    result = CitationValidator().validate(
        "L'information est insuffisante pour répondre.", []
    )
    assert result.is_valid


@pytest.mark.unit
def test_validator_flags_fabricated_citation_number() -> None:
    """Un numéro cité absent des citations fournies est invalide (Exigence 14.4)."""
    result = CitationValidator().validate(
        "Le taux atteint 12 % [3].", _citations(1, 2)
    )
    assert not result.is_valid
    assert result.invalid_citation_numbers == (3,)


@pytest.mark.unit
def test_validator_multiple_sentences_mixed() -> None:
    """Un mélange sourcé/non sourcé n'isole que la phrase non sourcée (Exigence 14.5)."""
    answer = (
        "Le budget prévu est de 500 millions [1]. "
        "Le délai est estimé à 4 ans."
    )
    result = CitationValidator().validate(answer, _citations(1))
    assert not result.is_valid
    assert len(result.unsourced_claims) == 1
    assert "4 ans" in result.unsourced_claims[0]
    assert result.cited_numbers == (1,)
