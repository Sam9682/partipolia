"""Fusion des scores de la recherche hybride (Exigence 12.2, Property 8).

Ce module implémente :class:`ScoreFusion`, qui combine les quatre sous-scores
d'un :class:`~app.rag.types.Candidate` en un unique score fusionné selon des
poids **configurables** :

    score = 0.40 × sémantique + 0.30 × lexical + 0.20 × qualité_de_source
            + 0.10 × récence

Invariant (Property 8 / Exigence 12.2) : la **somme des poids vaut exactement
1.0**. Les poids par défaut proviennent de :attr:`ScoreFusion.WEIGHTS` (alignés
sur la conception) et peuvent être surchargés par configuration
(``settings.fusion_weights`` — cf. :mod:`app.core.config`) ou par argument. Toute
combinaison dont la somme s'écarte de 1.0 est rejetée à la construction : le
score fusionné reste ainsi une **combinaison convexe** des sous-scores, donc
borné dans ``[0, 1]`` lorsque les sous-scores le sont.

Le :class:`Reranker` (réduction 20 → 6) fait l'objet d'une tâche distincte.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

from app.rag.types import Candidate, ScoredChunk

# Tolérance flottante pour la vérification de la somme des poids (ex. 0.1 + 0.2).
_WEIGHT_SUM_TOLERANCE: Final = 1e-9

# Composantes reconnues du score fusionné (ordre déterministe).
_COMPONENTS: Final = ("semantic", "lexical", "source_quality", "recency")


class ScoreFusion:
    """Fusionne les sous-scores d'un candidat en un score unique (Exigence 12.2).

    Les poids sont configurables ; leur somme doit valoir 1.0 (Property 8). Les
    poids par défaut sont ceux de la conception (:attr:`WEIGHTS`).
    """

    # Poids par défaut alignés sur la conception (Exigence 12.2, Property 8).
    WEIGHTS: Mapping[str, float] = {
        "semantic": 0.40,
        "lexical": 0.30,
        "source_quality": 0.20,
        "recency": 0.10,
    }

    def __init__(self, weights: Mapping[str, float] | None = None) -> None:
        """Initialise la fusion avec des ``weights`` (par défaut :attr:`WEIGHTS`).

        Les poids fournis doivent couvrir exactement les quatre composantes
        (``semantic``, ``lexical``, ``source_quality``, ``recency``) et sommer à
        1.0 (Property 8) ; sinon une :class:`ValueError` est levée.
        """
        resolved = dict(weights if weights is not None else self.WEIGHTS)
        self._validate_weights(resolved)
        # Copie figée pour empêcher toute mutation ultérieure des poids validés.
        self._weights: Mapping[str, float] = dict(resolved)

    @staticmethod
    def _validate_weights(weights: Mapping[str, float]) -> None:
        """Vérifie que ``weights`` couvre les composantes et somme à 1.0 (Property 8)."""
        missing = [name for name in _COMPONENTS if name not in weights]
        if missing:
            raise ValueError(
                "Poids de fusion manquants pour les composantes : "
                f"{', '.join(missing)}."
            )
        unknown = [name for name in weights if name not in _COMPONENTS]
        if unknown:
            raise ValueError(
                "Poids de fusion inconnus : " f"{', '.join(sorted(unknown))}."
            )
        total = sum(weights[name] for name in _COMPONENTS)
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ValueError(
                "La somme des poids de fusion doit valoir 1.0 "
                f"(actuellement {total!r})."
            )

    @property
    def weights(self) -> Mapping[str, float]:
        """Poids de fusion effectifs (lecture seule)."""
        return dict(self._weights)

    def score(self, candidate: Candidate) -> float:
        """Calcule le score fusionné d'un unique ``candidate`` (Exigence 12.2)."""
        w = self._weights
        return (
            w["semantic"] * candidate.semantic
            + w["lexical"] * candidate.lexical
            + w["source_quality"] * candidate.source_quality
            + w["recency"] * candidate.recency
        )

    def fuse(self, candidates: Iterable[Candidate]) -> list[ScoredChunk]:
        """Fusionne les sous-scores de ``candidates`` puis trie par score décroissant.

        Chaque :class:`~app.rag.types.Candidate` devient un
        :class:`~app.rag.types.ScoredChunk` porteur du score combiné (Exigence
        12.2). En cas d'égalité de score, l'ordre est stabilisé par ``chunk_id``
        croissant pour un résultat déterministe.
        """
        scored = [
            ScoredChunk(candidate=candidate, score=self.score(candidate))
            for candidate in candidates
        ]
        scored.sort(key=lambda item: (-item.score, item.chunk_id))
        return scored


__all__ = ["ScoreFusion"]
