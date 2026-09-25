"""Module RAG : classification, recherche hybride, fusion, reranking, génération.

Expose les briques de récupération et de classement (Exigence 12) :

* :class:`~app.rag.retriever.HybridSearch` — recherche lexicale (tsvector/tsquery)
  et sémantique (pgvector, distance cosinus), produisant des
  :class:`~app.rag.types.Candidate` porteurs de sous-scores normalisés ;
* :class:`~app.rag.ranking.ScoreFusion` — fusion configurable des sous-scores
  (0.40 sémantique + 0.30 lexical + 0.20 qualité + 0.10 récence, somme = 1.0) ;
* :class:`~app.rag.reranking.Reranker` — reranking final réduisant les ~20
  meilleurs résultats fusionnés aux 6 meilleurs (Exigence 12.3).
"""

from app.rag.classifier import QuestionClassifier, QuestionType
from app.rag.context import CitationValidator, ContextBuilder
from app.rag.pipeline import (
    INSUFFICIENT_INFORMATION_MESSAGE,
    SYSTEM_PROMPT,
    RagPipeline,
)
from app.rag.ranking import ScoreFusion
from app.rag.reranking import Reranker
from app.rag.retriever import HybridSearch
from app.rag.rewriter import QueryRewriter
from app.rag.types import Candidate, Citation, ScoredChunk, ValidationResult

__all__ = [
    "HybridSearch",
    "ScoreFusion",
    "Reranker",
    "QuestionClassifier",
    "QuestionType",
    "QueryRewriter",
    "ContextBuilder",
    "CitationValidator",
    "RagPipeline",
    "SYSTEM_PROMPT",
    "INSUFFICIENT_INFORMATION_MESSAGE",
    "Candidate",
    "ScoredChunk",
    "Citation",
    "ValidationResult",
]
