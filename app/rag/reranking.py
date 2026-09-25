"""Reranking des résultats de la recherche hybride (Exigence 12.3).

Ce module implémente :class:`Reranker`, dernière étape de classement du pipeline
de récupération : il **réduit les meilleurs candidats fusionnés à un sous-ensemble
restreint**, prêt pour la construction de contexte et la génération.

Contrat (Exigence 12.3) : à partir de la liste de :class:`~app.rag.types.ScoredChunk`
produite par :class:`~app.rag.ranking.ScoreFusion`, ``rerank`` conserve les
``keep`` meilleurs par **score décroissant** (par défaut 20 → 6). Le nombre
effectivement retenu vaut ``min(keep, len(scored))`` : une entrée plus courte que
``keep`` est renvoyée intégralement, sans complétion.

Déterminisme : le tri applique le même départage que la fusion — à score égal,
``chunk_id`` croissant — afin que la sélection soit reproductible quel que soit
l'ordre d'entrée.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from app.rag.types import ScoredChunk

# Nombre de fragments conservés par défaut à l'issue du reranking (Exigence 12.3).
DEFAULT_KEEP: Final = 6


class Reranker:
    """Réduit les résultats fusionnés aux ``keep`` meilleurs (Exigence 12.3).

    Étape terminale du classement : la recherche hybride remonte ~20 candidats,
    la fusion les ordonne, puis le reranking en conserve les 6 meilleurs pour le
    contexte de génération.
    """

    def rerank(
        self, scored: Iterable[ScoredChunk], *, keep: int = DEFAULT_KEEP
    ) -> list[ScoredChunk]:
        """Conserve les ``keep`` meilleurs ``scored`` par score décroissant.

        Le nombre retenu vaut ``min(keep, len(scored))`` : une liste plus courte
        que ``keep`` est renvoyée entière. Le classement est déterministe — tri
        par score décroissant, départage par ``chunk_id`` croissant — pour un
        résultat indépendant de l'ordre d'entrée.

        ``keep`` doit être un entier positif ou nul ; ``keep == 0`` renvoie une
        liste vide. Une valeur négative est rejetée par :class:`ValueError`.
        """
        if keep < 0:
            raise ValueError(
                f"Le nombre de fragments à conserver doit être >= 0 (reçu {keep!r})."
            )
        ordered = sorted(scored, key=lambda item: (-item.score, item.chunk_id))
        limit = min(keep, len(ordered))
        return ordered[:limit]


__all__ = ["Reranker", "DEFAULT_KEEP"]
