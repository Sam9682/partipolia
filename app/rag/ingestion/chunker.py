"""Découpage du texte en Chunks_De_Document (Exigences 11.4, 11.5, Property 6).

Dernière étape *métier pure* du pipeline avant la génération d'embeddings :
découpe un texte en fragments d'environ ``size_tokens`` (défaut 800) avec un
chevauchement de ``overlap_tokens`` (défaut 120) entre fragments consécutifs, et
attache à chaque fragment l'intégralité des métadonnées requises (Exigence
11.5) : ``source_id``, ``document_id``, ``page``, ``section``,
``publication_date``.

Invariants garantis (Property 6) :

* tous les chunks sauf éventuellement le dernier comptent exactement
  ``size_tokens`` tokens ;
* deux chunks consécutifs partagent ``overlap_tokens`` tokens (le pas d'avance
  vaut ``size_tokens - overlap_tokens``) ;
* chaque chunk porte les métadonnées complètes ;
* ``chunk_index`` est croissant à partir de 0.

Tokenisation : approximation par séparation sur les blancs (« mots »). Le
pipeline réel pourra brancher un tokeniseur de modèle, mais l'approximation par
mots suffit aux invariants de taille/chevauchement testés par propriété et évite
toute dépendance lourde.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass(frozen=True, slots=True)
class ChunkMetadata:
    """Métadonnées attachées à chaque chunk (Exigence 11.5).

    Reflètent les colonnes ``document_chunks.metadata`` (jsonb) : traçabilité de
    la Source, du Document, de la page, de la section et de la date de
    publication.
    """

    source_id: int | None = None
    document_id: int | None = None
    page: int | None = None
    section: str | None = None
    publication_date: date | None = None

    def as_dict(self) -> dict[str, Any]:
        """Sérialise les métadonnées pour la colonne jsonb (dates en ISO)."""
        return {
            "source_id": self.source_id,
            "document_id": self.document_id,
            "page": self.page,
            "section": self.section,
            "publication_date": (
                self.publication_date.isoformat()
                if self.publication_date is not None
                else None
            ),
        }


@dataclass(frozen=True, slots=True)
class Chunk:
    """Fragment de texte prêt à être vectorisé (Exigences 11.4, 11.5).

    ``token_count`` compte les tokens (mots) du fragment ; il vaut
    ``size_tokens`` pour tous les chunks sauf éventuellement le dernier.
    """

    chunk_index: int
    content: str
    token_count: int
    metadata: ChunkMetadata = field(default_factory=ChunkMetadata)


class Chunker:
    """Découpe un texte en :class:`Chunk` avec taille et chevauchement fixés."""

    def chunk(
        self,
        text: str,
        *,
        size_tokens: int = 800,
        overlap_tokens: int = 120,
        metadata: ChunkMetadata | None = None,
    ) -> list[Chunk]:
        """Découpe ``text`` en chunks de ``size_tokens`` (chevauchement ``overlap_tokens``).

        Le pas d'avance entre deux chunks vaut ``size_tokens - overlap_tokens``.
        Les métadonnées ``metadata`` sont recopiées sur chaque chunk (Exigence
        11.5). Un texte vide (ou uniquement des blancs) produit une liste vide.
        """
        if size_tokens <= 0:
            raise ValueError("size_tokens doit être strictement positif")
        if overlap_tokens < 0:
            raise ValueError("overlap_tokens ne peut pas être négatif")
        if overlap_tokens >= size_tokens:
            raise ValueError("overlap_tokens doit être strictement inférieur à size_tokens")

        meta = metadata or ChunkMetadata()

        # Tokenisation par mots (approximation). ``split()`` élimine les blancs
        # multiples et les blancs de bord de façon déterministe.
        tokens = text.split()
        if not tokens:
            return []

        stride = size_tokens - overlap_tokens
        chunks: list[Chunk] = []
        index = 0
        start = 0
        total = len(tokens)

        while start < total:
            window = tokens[start : start + size_tokens]
            chunks.append(
                Chunk(
                    chunk_index=index,
                    content=" ".join(window),
                    token_count=len(window),
                    metadata=meta,
                )
            )
            index += 1
            # Le dernier chunk est atteint dès que la fenêtre couvre la fin.
            if start + size_tokens >= total:
                break
            start += stride

        return chunks


__all__ = ["Chunk", "ChunkMetadata", "Chunker"]
