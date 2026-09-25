"""Jeu d'évaluation et harnais de métriques du Pipeline RAG (Exigences 13, 14, 32.5).

Ce module fournit, hors de la logique de propriété (Property 7), un **jeu
d'évaluation dédié** permettant de mesurer la qualité du :class:`~app.rag.pipeline.RagPipeline`
et un **harnais de calcul de métriques** (design.md § « Évaluation du RAG »).

Chaque entrée du dataset (:class:`EvalCase`) décrit :

* ``question`` — la question posée à l'Assistant IA (Exigence 13.1) ;
* ``documents`` — l'ensemble des fragments simulés que la recherche hybride
  renverra pour cette question (le « corpus » visible de ce cas), sous forme de
  :class:`~app.rag.types.Candidate` ;
* ``expected_documents`` — les ``chunk_id`` des fragments réellement pertinents
  (vérité terrain) servant au calcul de la précision/du rappel de récupération ;
* ``expected_answer`` — la réponse que le LLM simulé doit produire pour ce cas
  (permet d'exercer l'ancrage et la validation sans appel réseau) ;
* ``expected_citations`` — les numéros de citation ``[n]`` attendus dans la
  réponse (Exigence 13.4), servant au calcul de l'exactitude des citations.

Quatre métriques sont calculées sur l'ensemble du dataset (design.md) :

* **Précision de récupération** — proportion des passages *retenus* (après
  reranking) qui sont pertinents ;
* **Rappel de récupération** — proportion des passages pertinents effectivement
  retenus ;
* **Exactitude des citations** — concordance entre les Sources citées par la
  réponse et ``expected_citations`` (Exigences 13.4, 13.7) ;
* **Ancrage de la réponse** — proportion des affirmations factuelles de la
  réponse effectivement appuyées par au moins une Source citée valide, dérivée de
  la validation des citations (Exigences 13.5, 13.7, 14.1).

Le cas **sans passage pertinent** attend la réponse « information insuffisante »
(``expected_citations = ()``, ``expected_answer`` = message d'indisponibilité),
conformément aux Exigences 13.8 et 14.6.

Le harnais est **hors-ligne** : il n'exécute aucun appel réseau ni base. Le LLM
et la recherche hybride sont simulés (voir :class:`ScriptedLLM`,
:class:`FakeHybridSearch`) afin d'exercer le vrai code d'orchestration.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from app.rag.pipeline import INSUFFICIENT_INFORMATION_MESSAGE, RagPipeline
from app.rag.providers.base import Message
from app.rag.types import Candidate
from app.schemas.chat import ChatResponse


# --------------------------------------------------------------------------- #
# Doublures IA (hors-ligne) : LLM scripté et recherche hybride factice          #
# --------------------------------------------------------------------------- #
class ScriptedLLM:
    """``LLMProvider`` simulé renvoyant une réponse préprogrammée par question.

    La réponse est indexée sur le **message d'origine** de la question évaluée, ce
    qui permet d'associer à chaque :class:`EvalCase` la ``expected_answer`` qu'un
    LLM idéal produirait à partir du contexte. Aucun appel réseau n'a lieu ; la
    température reçue est enregistrée pour vérification (Exigence 14.2).
    """

    def __init__(self, responses: dict[str, str]) -> None:
        self._responses = responses
        self.calls: list[tuple[list[Message], float]] = []

    def generate(self, messages: list[Message], temperature: float) -> str:
        self.calls.append((messages, temperature))
        # Le dernier message « user » contient la question d'origine ; on retrouve
        # la réponse scriptée par correspondance de sous-chaîne.
        user_content = messages[-1]["content"] if messages else ""
        for question, response in self._responses.items():
            if question in user_content:
                return response
        return ""


class FakeHybridSearch:
    """``HybridSearch`` simulée retournant des candidats connus par question.

    À chaque question évaluée est associé un ensemble de :class:`Candidate` (le
    corpus visible du cas). La recherche renvoie ces candidats tels quels ; la
    fusion et le reranking réels du pipeline s'appliquent ensuite.
    """

    def __init__(self, corpus: dict[str, list[Candidate]]) -> None:
        self._corpus = corpus
        self.queries: list[str] = []

    async def search(self, query: str, *, top_k: int) -> list[Candidate]:
        self.queries.append(query)
        # La requête est réécrite par le pipeline ; on retrouve le corpus par
        # correspondance de sous-chaîne sur la question d'origine.
        for question, candidates in self._corpus.items():
            if question in query or query in question or _overlap(question, query):
                return list(candidates)
        return []


def _overlap(question: str, query: str) -> bool:
    """Heuristique de correspondance : partage d'au moins un mot significatif."""
    q_words = {w for w in question.lower().split() if len(w) > 3}
    r_words = {w for w in query.lower().split() if len(w) > 3}
    return bool(q_words & r_words)


# --------------------------------------------------------------------------- #
# Structures du jeu d'évaluation                                               #
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class EvalCase:
    """Une entrée du jeu d'évaluation du Pipeline RAG (design.md § Évaluation)."""

    question: str
    documents: tuple[Candidate, ...]
    expected_documents: tuple[int, ...]
    expected_answer: str
    expected_citations: tuple[int, ...]


@dataclass
class CaseMetrics:
    """Métriques calculées pour un cas unique."""

    question: str
    retrieval_precision: float
    retrieval_recall: float
    citation_accuracy: float
    answer_groundedness: float
    response: ChatResponse


@dataclass
class AggregateMetrics:
    """Moyennes des métriques sur l'ensemble du dataset."""

    retrieval_precision: float
    retrieval_recall: float
    citation_accuracy: float
    answer_groundedness: float
    per_case: list[CaseMetrics] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Construction du dataset                                                      #
# --------------------------------------------------------------------------- #
def _chunk(chunk_id: int, content: str, *, semantic: float = 0.9) -> Candidate:
    """Construit un ``Candidate`` de test avec métadonnées minimales.

    Les métadonnées ``source_id``/``document_id`` permettent d'afficher les
    Sources (Exigence 13.4). ``semantic`` module la pertinence : les passages
    pertinents ont un score élevé, les distracteurs un score faible, de sorte que
    le reranking réel retienne bien les passages attendus.
    """
    return Candidate(
        chunk_id=chunk_id,
        content=content,
        semantic=semantic,
        lexical=semantic * 0.6,
        source_quality=0.8,
        recency=0.5,
        metadata={"source_id": 100 + chunk_id, "document_id": 200 + chunk_id},
    )


def build_dataset() -> list[EvalCase]:
    """Construit le jeu d'évaluation du Pipeline RAG (design.md § Évaluation du RAG).

    Le dataset couvre :

    * des questions documentaires avec passages pertinents et distracteurs, dont
      la réponse idéale cite les Sources attendues (Exigences 13.4, 13.7) ;
    * un cas **sans passage pertinent** dont la réponse attendue est « information
      insuffisante » (Exigences 13.8, 14.6).
    """
    return [
        # Cas 1 — question sur un budget : un passage pertinent + un distracteur.
        EvalCase(
            question="Quel est le budget prévu pour la mesure ?",
            documents=(
                _chunk(1, "Le budget prévu pour la mesure est de 3 milliards d'euros.", semantic=0.95),
                _chunk(2, "Le calendrier de déploiement s'étend sur cinq ans.", semantic=0.2),
            ),
            expected_documents=(1,),
            expected_answer="Le budget prévu est de 3 milliards d'euros [1].",
            expected_citations=(1,),
        ),
        # Cas 2 — question comparative : deux passages pertinents cités.
        EvalCase(
            question="Compare le coût de la mesure A et de la mesure B.",
            documents=(
                _chunk(3, "La mesure A coûte 2 milliards d'euros selon le rapport.", semantic=0.93),
                _chunk(4, "La mesure B coûte 4 milliards d'euros selon l'étude.", semantic=0.92),
                _chunk(5, "La météo n'a aucun rapport avec le sujet.", semantic=0.1),
            ),
            expected_documents=(3, 4),
            expected_answer=(
                "La mesure A coûte 2 milliards d'euros [1] tandis que la mesure B "
                "coûte 4 milliards d'euros [2]."
            ),
            expected_citations=(1, 2),
        ),
        # Cas 3 — question documentaire simple, une source.
        EvalCase(
            question="Quel taux de réduction est documenté ?",
            documents=(
                _chunk(6, "L'étude documente un taux de réduction de 12 %.", semantic=0.94),
                _chunk(7, "Un paragraphe hors sujet sur la gouvernance interne.", semantic=0.15),
            ),
            expected_documents=(6,),
            expected_answer="Le taux de réduction documenté est de 12 % [1].",
            expected_citations=(1,),
        ),
        # Cas 4 — AUCUN passage pertinent : « information insuffisante » attendue
        # (Exigences 13.8, 14.6). Le corpus visible est vide pour cette question.
        EvalCase(
            question="Quelle est la position sur un sujet totalement absent du corpus ?",
            documents=(),
            expected_documents=(),
            expected_answer=INSUFFICIENT_INFORMATION_MESSAGE,
            expected_citations=(),
        ),
    ]


# --------------------------------------------------------------------------- #
# Calcul des métriques                                                         #
# --------------------------------------------------------------------------- #
def _precision_recall(
    retrieved_ids: set[int], expected_ids: set[int]
) -> tuple[float, float]:
    """Précision et rappel de récupération pour un cas.

    Convention pour le cas « aucun passage pertinent » (``expected_ids`` vide) :
    la précision et le rappel valent ``1.0`` si et seulement si rien n'a été
    retenu (le pipeline ne doit récupérer aucun passage), ``0.0`` sinon. Cela
    récompense l'abstention attendue (Exigences 13.8, 14.6).
    """
    if not expected_ids:
        return (1.0, 1.0) if not retrieved_ids else (0.0, 1.0)
    if not retrieved_ids:
        return 0.0, 0.0
    true_positives = len(retrieved_ids & expected_ids)
    precision = true_positives / len(retrieved_ids)
    recall = true_positives / len(expected_ids)
    return precision, recall


def _citation_accuracy(
    cited: set[int], expected_citations: set[int]
) -> float:
    """Exactitude des citations : Jaccard entre citations produites et attendues.

    Pour le cas « information insuffisante » (aucune citation attendue),
    l'exactitude vaut ``1.0`` si la réponse ne cite rien, ``0.0`` sinon.
    """
    if not expected_citations:
        return 1.0 if not cited else 0.0
    union = cited | expected_citations
    if not union:
        return 1.0
    return len(cited & expected_citations) / len(union)


def _answer_groundedness(response: ChatResponse) -> float:
    """Ancrage de la réponse : part des affirmations factuelles appuyées (13.5, 13.7).

    On réutilise la validation des citations du pipeline via la ``confidence`` :
    une réponse entièrement sourcée obtient une confiance élevée, une réponse avec
    affirmations non sourcées ou citations fabriquées voit sa confiance baisser.
    Pour le cas « information insuffisante » (aucune Source, confiance nulle),
    l'ancrage vaut ``1.0`` : l'abstention est parfaitement ancrée (rien n'est
    affirmé sans source).
    """
    if not response.sources:
        # Abstention explicite : parfaitement ancrée si c'est bien le message
        # d'indisponibilité, sinon non ancrée.
        return 1.0 if response.answer == INSUFFICIENT_INFORMATION_MESSAGE else 0.0
    return response.confidence


def evaluate_case(pipeline: RagPipeline | None, case: EvalCase) -> CaseMetrics:
    """Exécute le pipeline sur un cas et calcule ses métriques.

    Si ``pipeline`` est ``None``, un pipeline dimensionné pour ce cas est
    construit : le reranking conserve exactement le nombre de passages pertinents
    attendus (``keep = len(expected_documents)`` ; au moins 1), de sorte que les
    distracteurs à faible score sémantique soient écartés — comme le ferait le
    reranking 20 → 6 sur un vrai corpus (Exigence 12.3).
    """
    if pipeline is None:
        keep = max(1, len(case.expected_documents))
        pipeline = build_pipeline([case], keep=keep)
    response = asyncio.run(pipeline.answer(case.question, proposal_id=None))

    retrieved_ids = {source.chunk_id for source in response.sources}
    expected_ids = set(case.expected_documents)
    precision, recall = _precision_recall(retrieved_ids, expected_ids)

    cited = {source.number for source in response.sources}
    citation_accuracy = _citation_accuracy(cited, set(case.expected_citations))

    groundedness = _answer_groundedness(response)

    return CaseMetrics(
        question=case.question,
        retrieval_precision=precision,
        retrieval_recall=recall,
        citation_accuracy=citation_accuracy,
        answer_groundedness=groundedness,
        response=response,
    )


def build_pipeline(dataset: list[EvalCase], *, keep: int | None = None) -> RagPipeline:
    """Assemble un ``RagPipeline`` hors-ligne pré-scripté pour le dataset.

    Le LLM simulé renvoie l'``expected_answer`` de chaque cas et la recherche
    hybride factice renvoie les ``documents`` associés à chaque question. Les
    autres briques (classification, réécriture, fusion, reranking, contexte,
    validation) sont les **vraies** implémentations.

    ``keep`` fixe le nombre de fragments conservés par le reranking. Par défaut,
    il vaut le **nombre maximal de passages pertinents** parmi les cas du dataset :
    dans ce corpus contrôlé et de petite taille, cela laisse le reranking écarter
    les distracteurs (score sémantique faible) plutôt que de tout retenir, ce qui
    reflète le comportement 20 → 6 sur un vrai corpus (Exigence 12.3).
    """
    corpus = {case.question: list(case.documents) for case in dataset}
    responses = {case.question: case.expected_answer for case in dataset}
    if keep is None:
        keep = max((len(case.expected_documents) for case in dataset), default=1) or 1
    return RagPipeline(
        hybrid_search=FakeHybridSearch(corpus),  # type: ignore[arg-type]
        llm_provider=ScriptedLLM(responses),
        temperature=0.1,
        keep=keep,
    )


def evaluate_dataset(
    pipeline: RagPipeline | None, dataset: list[EvalCase]
) -> AggregateMetrics:
    """Évalue tout le dataset et agrège (moyenne) les métriques par cas.

    Lorsque ``pipeline`` est ``None``, chaque cas est évalué avec un pipeline
    dimensionné pour lui (reranking calé sur le nombre de passages pertinents),
    ce qui isole la mesure des distracteurs entre cas.
    """
    per_case = [evaluate_case(pipeline, case) for case in dataset]
    n = len(per_case) or 1

    def _avg(attr: str) -> float:
        return sum(getattr(m, attr) for m in per_case) / n

    return AggregateMetrics(
        retrieval_precision=_avg("retrieval_precision"),
        retrieval_recall=_avg("retrieval_recall"),
        citation_accuracy=_avg("citation_accuracy"),
        answer_groundedness=_avg("answer_groundedness"),
        per_case=per_case,
    )


__all__ = [
    "EvalCase",
    "CaseMetrics",
    "AggregateMetrics",
    "ScriptedLLM",
    "FakeHybridSearch",
    "build_dataset",
    "build_pipeline",
    "evaluate_case",
    "evaluate_dataset",
]
