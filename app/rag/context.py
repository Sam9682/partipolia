"""Construction du contexte et validation des citations (Exigences 13.4, 14.3–14.5).

Ce module regroupe les deux briques de fin de pipeline qui encadrent la génération
LLM (Exigence 13.3) :

* :class:`ContextBuilder` — à partir des fragments retenus par le reranking
  (:class:`~app.rag.types.ScoredChunk`), construit la chaîne de contexte
  transmise au LLM **avec des citations numérotées** ``[1] … [n]`` et la liste
  ordonnée des :class:`~app.rag.types.Citation` correspondantes (Exigence 13.4) ;
* :class:`CitationValidator` — après génération, **détecte les affirmations
  factuelles non sourcées** de la réponse (aucun marqueur ``[n]`` valide) et
  signale les numéros de citation fabriqués (hors des citations fournies),
  produisant un :class:`~app.rag.types.ValidationResult` (Exigences 14.3–14.5).

La validation est **conservatrice** : une phrase porteuse d'un signal factuel
(nombre, pourcentage, montant, date, vocabulaire chiffré) qui ne référence aucune
citation valide est considérée comme non sourcée. Une phrase d'opinion ou de mise
en garde (« il n'y a pas d'information suffisante ») n'est pas pénalisée. Ce
mécanisme matérialise les garde-fous anti-hallucination : exiger des citations
pour les affirmations factuelles (14.3), vérifier les citations produites (14.4),
détecter toute affirmation factuelle non sourcée (14.5).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from typing import Final

from app.rag.types import Citation, ScoredChunk, ValidationResult

# Repère les marqueurs de citation « [n] » (n entier positif) dans une réponse.
_CITATION_MARKER: Final = re.compile(r"\[(\d+)\]")

# Découpage naïf en phrases sur la ponctuation forte (. ! ? ; retour ligne).
_SENTENCE_SPLIT: Final = re.compile(r"[.!?;\n]+")

# Signaux d'une affirmation factuelle : nombres, %, montants, dates, unités.
_NUMBER_SIGNAL: Final = re.compile(
    r"\d|%|€|\$|\bmilliard|\bmillion|\bpourcent|\bpour cent",
    flags=re.IGNORECASE,
)

# Vocabulaire factuel courant (normalisé, sans accents) renforçant la détection.
_FACTUAL_TERMS: Final[tuple[str, ...]] = (
    "cout",
    "budget",
    "taux",
    "chiffre",
    "montant",
    "statistique",
    "etude",
    "rapport",
    "mesure",
    "estimation",
    "estime",
    "prevu",
    "augmente",
    "diminue",
    "reduit",
    "represente",
    "s'eleve",
    "atteint",
)

# Expressions signalant une absence assumée d'information : jamais « non sourcées ».
_INSUFFICIENT_MARKERS: Final[tuple[str, ...]] = (
    "information insuffisante",
    "information indisponible",
    "pas d'information",
    "aucune information",
    "je ne peux pas",
    "je ne dispose pas",
    "aucune source",
)


def _normalize(text: str) -> str:
    """Minuscule sans accents (NFKD) pour une détection lexicale robuste."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


class ContextBuilder:
    """Construit le contexte cité pour la génération (Exigence 13.4)."""

    def build(self, chunks: Iterable[ScoredChunk]) -> tuple[str, list[Citation]]:
        """Assemble le contexte numéroté et la liste des citations (Exigence 13.4).

        Chaque fragment reçoit un numéro croissant à partir de 1 (sans trou),
        matérialisé dans le contexte par un bloc préfixé ``[n]`` et repris dans la
        :class:`~app.rag.types.Citation` associée. L'ordre suit celui des
        ``chunks`` fournis (déjà classés par le reranking). Une entrée vide produit
        un contexte vide et aucune citation.
        """
        blocks: list[str] = []
        citations: list[Citation] = []
        for number, scored in enumerate(chunks, start=1):
            candidate = scored.candidate
            metadata = candidate.metadata or {}
            content = candidate.content.strip()
            citations.append(
                Citation(
                    number=number,
                    chunk_id=candidate.chunk_id,
                    content=content,
                    source_id=_as_int(metadata.get("source_id")),
                    document_id=_as_int(metadata.get("document_id")),
                    metadata=dict(metadata) if metadata else None,
                )
            )
            blocks.append(f"[{number}] {content}")
        return "\n\n".join(blocks), citations


class CitationValidator:
    """Détecte les affirmations factuelles non sourcées d'une réponse (Exigence 14.5).

    La validation vérifie que toute phrase porteuse d'un signal factuel référence
    au moins une citation ``[n]`` **valide** (c.-à-d. présente dans les citations
    fournies). Elle relève également les numéros ``[n]`` cités mais inexistants.
    """

    def validate(
        self, answer: str, citations: Sequence[Citation]
    ) -> ValidationResult:
        """Valide les citations de ``answer`` (Exigences 14.3–14.5).

        Renvoie un :class:`~app.rag.types.ValidationResult` : ``is_valid`` est vrai
        si aucune affirmation factuelle non sourcée n'est détectée **et** si aucun
        numéro de citation fabriqué n'est employé. ``unsourced_claims`` liste les
        phrases factuelles sans citation valide ; ``cited_numbers`` les numéros
        effectivement référencés ; ``invalid_citation_numbers`` ceux hors périmètre.
        """
        valid_numbers = {citation.number for citation in citations}

        all_cited: set[int] = set()
        invalid_cited: set[int] = set()
        unsourced: list[str] = []

        for sentence in self._sentences(answer):
            cited_here = self._cited_numbers(sentence)
            all_cited.update(cited_here)
            invalid_here = cited_here - valid_numbers
            invalid_cited.update(invalid_here)

            if not self._is_factual(sentence):
                continue
            # Une affirmation factuelle doit référencer >= 1 citation valide.
            if not (cited_here & valid_numbers):
                unsourced.append(sentence.strip())

        is_valid = not unsourced and not invalid_cited
        return ValidationResult(
            is_valid=is_valid,
            unsourced_claims=tuple(unsourced),
            cited_numbers=tuple(sorted(all_cited)),
            invalid_citation_numbers=tuple(sorted(invalid_cited)),
        )

    @staticmethod
    def _sentences(answer: str) -> list[str]:
        """Découpe ``answer`` en phrases non vides (ponctuation forte)."""
        return [part for part in _SENTENCE_SPLIT.split(answer) if part.strip()]

    @staticmethod
    def _cited_numbers(sentence: str) -> set[int]:
        """Extrait les numéros de citation ``[n]`` d'une phrase."""
        return {int(match) for match in _CITATION_MARKER.findall(sentence)}

    @staticmethod
    def _is_factual(sentence: str) -> bool:
        """Indique si une phrase porte un signal factuel devant être sourcé.

        Une phrase signalant explicitement une absence d'information n'est jamais
        considérée factuelle (elle assume l'indisponibilité, Exigence 14.6).
        """
        normalized = _normalize(sentence)
        if any(marker in normalized for marker in _INSUFFICIENT_MARKERS):
            return False
        if _NUMBER_SIGNAL.search(sentence):
            return True
        return any(term in normalized for term in _FACTUAL_TERMS)


def _as_int(value: object) -> int | None:
    """Convertit prudemment ``value`` en ``int`` (ou ``None`` si impossible)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    return None


__all__ = ["ContextBuilder", "CitationValidator"]
