"""Évaluation du Pipeline RAG sur un jeu d'évaluation dédié (Exigences 13, 14, 32.5).

Ces tests exercent le **vrai** code d'orchestration du ``RagPipeline`` sur le jeu
d'évaluation défini dans :mod:`tests.rag.rag_eval_dataset`, en ne remplaçant que
les Providers IA (LLM scripté, recherche hybride factice), afin de mesurer :

* la **précision** et le **rappel de récupération** — les passages retenus
  correspondent aux passages pertinents attendus (``expected_documents``) ;
* l'**exactitude des citations** — les Sources citées correspondent aux
  ``expected_citations`` (Exigences 13.4, 13.7) ;
* l'**ancrage de la réponse** — les affirmations factuelles sont appuyées par au
  moins une Source citée valide (Exigences 13.5, 13.7, 14.1).

Un cas dédié vérifie que, **sans passage pertinent**, la réponse est
« information insuffisante » (Exigences 13.8, 14.6). Tout est hors-ligne : aucun
appel réseau ni base (conforme à la convention de stubbing des ``conftest.py``).
"""

from __future__ import annotations

import pytest

from app.rag.pipeline import INSUFFICIENT_INFORMATION_MESSAGE
from tests.rag.rag_eval_dataset import (
    build_dataset,
    evaluate_case,
    evaluate_dataset,
)


# --------------------------------------------------------------------------- #
# Structure du dataset                                                         #
# --------------------------------------------------------------------------- #
def test_dataset_has_required_fields_and_no_passage_case() -> None:
    """Chaque cas porte les champs requis ; un cas « sans passage » existe (design.md)."""
    dataset = build_dataset()
    assert dataset, "le jeu d'évaluation ne doit pas être vide"

    for case in dataset:
        assert case.question.strip()
        # expected_documents / expected_citations sont des tuples (éventuellement vides).
        assert isinstance(case.expected_documents, tuple)
        assert isinstance(case.expected_citations, tuple)
        assert isinstance(case.expected_answer, str) and case.expected_answer

    # Au moins un cas sans passage pertinent → « information insuffisante ».
    no_passage = [c for c in dataset if not c.expected_documents]
    assert no_passage, "un cas sans passage pertinent est requis (Exigences 13.8, 14.6)"
    assert all(c.expected_citations == () for c in no_passage)
    assert all(
        c.expected_answer == INSUFFICIENT_INFORMATION_MESSAGE for c in no_passage
    )


# --------------------------------------------------------------------------- #
# Métriques agrégées sur le dataset                                            #
# --------------------------------------------------------------------------- #
def test_aggregate_metrics_meet_quality_thresholds() -> None:
    """Le pipeline atteint des métriques élevées sur le jeu d'évaluation."""
    dataset = build_dataset()

    # ``None`` : chaque cas est évalué avec un pipeline dimensionné pour lui
    # (reranking calé sur le nombre de passages pertinents), écartant les
    # distracteurs comme le ferait un vrai corpus 20 → 6 (Exigence 12.3).
    metrics = evaluate_dataset(None, dataset)

    # Le corpus scénarisé écarte les distracteurs (faible score) : récupération
    # parfaite attendue sur ce dataset contrôlé.
    assert metrics.retrieval_precision == pytest.approx(1.0)
    assert metrics.retrieval_recall == pytest.approx(1.0)
    # Les réponses scriptées citent exactement les Sources attendues.
    assert metrics.citation_accuracy == pytest.approx(1.0)
    # Réponses entièrement sourcées (ou abstention ancrée) → ancrage élevé.
    assert metrics.answer_groundedness >= 0.9


# --------------------------------------------------------------------------- #
# Précision / rappel de récupération par cas                                   #
# --------------------------------------------------------------------------- #
def test_retrieval_recovers_expected_documents_per_case() -> None:
    """Pour chaque cas avec passages, les Sources retenues couvrent les attendus."""
    dataset = build_dataset()

    for case in dataset:
        if not case.expected_documents:
            continue
        result = evaluate_case(None, case)
        retrieved = {s.chunk_id for s in result.response.sources}
        assert set(case.expected_documents) <= retrieved
        assert result.retrieval_precision == pytest.approx(1.0)
        assert result.retrieval_recall == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Exactitude des citations                                                     #
# --------------------------------------------------------------------------- #
def test_citation_accuracy_matches_expected_citations() -> None:
    """Les numéros de citation produits correspondent aux citations attendues (13.4)."""
    dataset = build_dataset()

    for case in dataset:
        result = evaluate_case(None, case)
        cited = {s.number for s in result.response.sources}
        assert cited == set(case.expected_citations)
        assert result.citation_accuracy == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Cas sans passage → « information insuffisante » (Exigences 13.8, 14.6)        #
# --------------------------------------------------------------------------- #
def test_no_relevant_passage_yields_insufficient_information() -> None:
    """Sans passage pertinent : réponse « information insuffisante », aucune Source."""
    dataset = build_dataset()

    no_passage_case = next(c for c in dataset if not c.expected_documents)
    result = evaluate_case(None, no_passage_case)

    assert result.response.answer == INSUFFICIENT_INFORMATION_MESSAGE
    assert result.response.sources == []
    assert result.response.confidence == 0.0
    # Abstention correctement mesurée : rien récupéré, rien cité, ancrage parfait.
    assert result.retrieval_precision == pytest.approx(1.0)
    assert result.retrieval_recall == pytest.approx(1.0)
    assert result.citation_accuracy == pytest.approx(1.0)
    assert result.answer_groundedness == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Ancrage : une réponse mal citée fait chuter l'ancrage                         #
# --------------------------------------------------------------------------- #
def test_groundedness_penalises_unsourced_answer() -> None:
    """Une réponse factuelle non sourcée obtient un ancrage < réponse sourcée (14.5)."""
    from tests.rag.rag_eval_dataset import EvalCase, _chunk

    # Même passage récupéré, mais la réponse scriptée n'insère aucune citation.
    grounded = EvalCase(
        question="Question sourcée sur le budget chiffré ?",
        documents=(_chunk(50, "Le budget est de 7 milliards d'euros."),),
        expected_documents=(50,),
        expected_answer="Le budget est de 7 milliards d'euros [1].",
        expected_citations=(1,),
    )
    ungrounded = EvalCase(
        question="Question non sourcée sur le budget chiffré ?",
        documents=(_chunk(51, "Le budget est de 7 milliards d'euros."),),
        expected_documents=(51,),
        expected_answer="Le budget est de 7 milliards d'euros.",  # aucune citation
        expected_citations=(1,),
    )
    grounded_result = evaluate_case(None, grounded)
    ungrounded_result = evaluate_case(None, ungrounded)

    assert grounded_result.answer_groundedness > ungrounded_result.answer_groundedness


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
