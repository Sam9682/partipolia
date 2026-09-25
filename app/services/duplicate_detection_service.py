"""Service de détection de doublons à la création d'une Proposition (Exigence 5).

Ce Service implémente le contrat ``DuplicateDetectionService`` de la conception :

```python
class DuplicateDetectionService:
    def find_similar(self, title: str, description: str, *, threshold: float,
                     top_k: int) -> list[SimilarProposal]: ...
```

Principe (Exigence 5.1) : à la création d'une Proposition, la Plateforme calcule
un **embedding** du texte de la Proposition (titre + description) via
l'``EmbeddingProvider`` sélectionné par configuration (Exigence 16.3), puis
recherche les Propositions existantes proches par **similarité cosinus**. Les
Propositions dont la similarité atteint ``threshold`` sont retournées, triées par
similarité décroissante et limitées à ``top_k``.

Les embeddings des Propositions candidates sont calculés à la volée à partir de
leur propre texte (titre + description) : les Propositions ne stockent pas de
vecteur (seuls les ``document_chunks`` en portent un — Exigence 11.6). Le Service
reste ainsi indépendant du schéma vectoriel et **testable hors-ligne** avec un
``EmbeddingProvider`` simulé.

Ce Service est purement consultatif : il ne bloque jamais la création (« Créer
quand même » — Exigence 5.3), la décision d'utiliser son résultat revenant à
l'appelant (``POST /proposals``).
"""

from __future__ import annotations

import math
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.proposal import Proposal
from app.rag.providers.base import EmbeddingProvider
from app.schemas.duplicate import SimilarProposal

# Seuil de similarité par défaut : deux Propositions au-delà sont jugées proches.
DEFAULT_THRESHOLD: Final = 0.82

# Nombre maximal de Propositions proches renvoyées par défaut.
DEFAULT_TOP_K: Final = 5

# Statuts des Propositions candidates au rapprochement : on ignore les
# Propositions archivées ou rejetées, non pertinentes comme doublons actifs.
_CANDIDATE_STATUSES: Final[tuple[str, ...]] = (
    "DRAFT",
    "PENDING_REVIEW",
    "PUBLISHED",
)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similarité cosinus de deux vecteurs, normalisée dans ``[0, 1]``.

    La similarité cosinus brute appartient à ``[-1, 1]`` ; elle est ramenée à
    ``[0, 1]`` via ``(1 + cos) / 2`` afin d'obtenir un score homogène et borné
    (0 = opposé, 1 = identique). Un vecteur nul ou de dimension incompatible
    donne ``0.0`` (aucune proximité mesurable).
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    cosine = dot / (norm_a * norm_b)
    # Borne défensive contre les erreurs d'arrondi flottant.
    cosine = max(-1.0, min(1.0, cosine))
    return (1.0 + cosine) / 2.0


def _proposal_text(title: str, description: str) -> str:
    """Compose le texte représentatif d'une Proposition pour l'embedding.

    Le titre et la description portent l'essentiel du contenu discriminant ; ils
    sont concaténés pour former une seule représentation textuelle vectorisée.
    """
    return f"{title.strip()}\n\n{description.strip()}".strip()


class DuplicateDetectionService:
    """Détecte les Propositions proches d'un brouillon en cours (Exigence 5.1).

    Instancié par requête avec une ``AsyncSession`` (pour lister les Propositions
    candidates) et un ``EmbeddingProvider`` (pour vectoriser les textes). Le
    fournisseur d'embeddings est sélectionné par configuration (Exigence 16.3) et
    injecté afin de rester testable hors-ligne.
    """

    def __init__(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider

    async def find_similar(
        self,
        title: str,
        description: str,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
        exclude_id: int | None = None,
    ) -> list[SimilarProposal]:
        """Retourne les Propositions proches du texte fourni (Exigence 5.1).

        Calcule l'embedding de ``title`` + ``description`` puis compare, par
        similarité cosinus, aux embeddings des Propositions candidates (statuts
        actifs). Ne conservent que celles dont la similarité atteint ``threshold``,
        triées par similarité décroissante et limitées à ``top_k``.

        ``exclude_id`` permet d'écarter une Proposition (par exemple celle qui
        vient d'être créée). Un ``top_k`` non positif ou un texte vide renvoie une
        liste vide sans appel au fournisseur d'embeddings.
        """
        if top_k <= 0:
            return []
        query_text = _proposal_text(title, description)
        if not query_text:
            return []

        candidates = await self._load_candidates(exclude_id)
        if not candidates:
            return []

        # Un seul appel au fournisseur pour l'ensemble des textes (requête + candidats).
        texts = [query_text] + [
            _proposal_text(row.title, row.description) for row in candidates
        ]
        vectors = self._embedding_provider.embed(texts)
        if not vectors or len(vectors) != len(texts):
            return []

        query_vector = list(vectors[0])
        scored: list[SimilarProposal] = []
        for row, vector in zip(candidates, vectors[1:]):
            similarity = _cosine_similarity(query_vector, list(vector))
            if similarity >= threshold:
                scored.append(
                    SimilarProposal(
                        id=row.id,
                        slug=row.slug,
                        title=row.title,
                        similarity=similarity,
                    )
                )

        # Tri par similarité décroissante puis id croissant (déterministe), tronqué.
        scored.sort(key=lambda item: (-item.similarity, item.id))
        return scored[:top_k]

    async def _load_candidates(self, exclude_id: int | None) -> list[Proposal]:
        """Charge les Propositions candidates au rapprochement (statuts actifs)."""
        stmt = select(Proposal).where(Proposal.status.in_(_CANDIDATE_STATUSES))
        if exclude_id is not None:
            stmt = stmt.where(Proposal.id != exclude_id)
        return list((await self._session.scalars(stmt)).all())


__all__ = [
    "DuplicateDetectionService",
    "DEFAULT_THRESHOLD",
    "DEFAULT_TOP_K",
]
