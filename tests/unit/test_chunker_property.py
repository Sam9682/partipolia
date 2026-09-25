# Feature: partipolia-platform, Property 6: Invariants de chunking et préservation des métadonnées
"""Test de propriété du ``Chunker`` (Exigences 11.4, 11.5).

**Property 6: Invariants de chunking et préservation des métadonnées**

**Validates: Requirements 11.4, 11.5**

Pour tout texte d'entrée, ``Chunker.chunk`` (taille ``size_tokens``,
chevauchement ``overlap_tokens``) produit des chunks dont les invariants sont :

* **Taille** — tous les chunks sauf éventuellement le dernier comptent
  exactement ``size_tokens`` tokens ; le dernier en compte au plus ``size_tokens``
  et au moins 1 (Exigence 11.4).
* **Chevauchement** — deux chunks consécutifs partagent exactement
  ``overlap_tokens`` tokens (le pas d'avance vaut ``size_tokens - overlap_tokens``)
  dès qu'il reste au moins ``size_tokens`` tokens à couvrir (Exigence 11.4).
* **Préservation des métadonnées** — chaque chunk porte l'intégralité des
  métadonnées requises : ``source_id``, ``document_id``, ``page``, ``section`` et
  ``publication_date`` (Exigence 11.5).
* **Aucune perte de contenu** — la réunion des chunks, en supprimant le
  chevauchement, reconstitue exactement la suite des tokens du texte : aucun token
  n'est perdu ni dupliqué à la frontière des chunks (Exigence 11.4).

Le test exerce le vrai code du :class:`Chunker` sans mock ni dépendance externe :
la tokenisation par mots (``str.split``) est celle du module. Les générateurs
Hypothesis produisent des textes réalistes (nombre variable de « mots ») ainsi
que des couples ``(size_tokens, overlap_tokens)`` valides (``0 <= overlap <
size``), afin de couvrir l'espace pertinent des invariants.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.ingestion.chunker import Chunk, ChunkMetadata, Chunker


# --- Générateurs Hypothesis -------------------------------------------------

# « Mots » simples sans blancs internes : la tokenisation par ``split`` produit
# alors exactement un token par mot, ce qui rend les invariants de taille
# vérifiables de façon déterministe.
_word = st.text(
    alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
    min_size=1,
    max_size=12,
)

# Listes de mots de taille variée : depuis le texte vide (0 mot) jusqu'à des
# textes couvrant plusieurs fenêtres pour exercer le chevauchement.
_words = st.lists(_word, min_size=0, max_size=60)


@st.composite
def _size_and_overlap(draw: st.DrawFn) -> tuple[int, int]:
    """Couple ``(size_tokens, overlap_tokens)`` valide : ``0 <= overlap < size``.

    On garde des tailles petites pour multiplier les frontières de chunks avec
    des textes courts, tout en restant dans les mêmes invariants que la
    configuration de production (800 / 120).
    """
    size = draw(st.integers(min_value=1, max_value=12))
    overlap = draw(st.integers(min_value=0, max_value=size - 1))
    return size, overlap


_metadata = st.builds(
    ChunkMetadata,
    source_id=st.integers(min_value=1, max_value=10_000),
    document_id=st.integers(min_value=1, max_value=10_000),
    page=st.integers(min_value=1, max_value=500),
    section=st.text(min_size=0, max_size=20),
    publication_date=st.dates(min_value=date(2000, 1, 1), max_value=date(2030, 12, 31)),
)


def _reconstruct_tokens(chunks: list[Chunk], overlap_tokens: int) -> list[str]:
    """Reconstitue la suite des tokens en retirant le chevauchement entre chunks.

    Le premier chunk est pris intégralement ; pour chaque chunk suivant, on
    ignore ses ``overlap_tokens`` premiers tokens (partagés avec le précédent).
    """
    if not chunks:
        return []
    tokens: list[str] = chunks[0].content.split()
    for chunk in chunks[1:]:
        tokens.extend(chunk.content.split()[overlap_tokens:])
    return tokens


@pytest.mark.property
@settings(max_examples=200, deadline=None)
@given(words=_words, size_overlap=_size_and_overlap(), metadata=_metadata)
async def test_chunk_invariants_and_metadata_preservation(
    words: list[str], size_overlap: tuple[int, int], metadata: ChunkMetadata
) -> None:
    """Invariants de taille/chevauchement, métadonnées complètes, aucune perte."""
    size_tokens, overlap_tokens = size_overlap
    text = " ".join(words)
    tokens = text.split()

    chunks = Chunker().chunk(
        text,
        size_tokens=size_tokens,
        overlap_tokens=overlap_tokens,
        metadata=metadata,
    )

    # Un texte sans token ne produit aucun chunk.
    if not tokens:
        assert chunks == []
        return

    # Au moins un chunk dès qu'il y a des tokens.
    assert chunks

    for position, chunk in enumerate(chunks):
        # chunk_index croissant à partir de 0.
        assert chunk.chunk_index == position
        # token_count cohérent avec le contenu.
        assert chunk.token_count == len(chunk.content.split())
        # Taille : tous sauf le dernier valent exactement size_tokens ;
        # le dernier est dans [1, size_tokens] (Exigence 11.4).
        if position < len(chunks) - 1:
            assert chunk.token_count == size_tokens
        else:
            assert 1 <= chunk.token_count <= size_tokens
        # Préservation intégrale des métadonnées sur chaque chunk (Exigence 11.5).
        assert chunk.metadata is metadata
        assert chunk.metadata.source_id == metadata.source_id
        assert chunk.metadata.document_id == metadata.document_id
        assert chunk.metadata.page == metadata.page
        assert chunk.metadata.section == metadata.section
        assert chunk.metadata.publication_date == metadata.publication_date

    # Chevauchement : deux chunks consécutifs partagent overlap_tokens tokens
    # (Exigence 11.4).
    stride = size_tokens - overlap_tokens
    for previous, current in zip(chunks, chunks[1:], strict=False):
        previous_tokens = previous.content.split()
        current_tokens = current.content.split()
        if overlap_tokens > 0:
            assert previous_tokens[stride:] == current_tokens[:overlap_tokens]

    # Aucune perte de contenu : en retirant le chevauchement, on reconstitue
    # exactement la suite des tokens du texte (Exigence 11.4).
    assert _reconstruct_tokens(chunks, overlap_tokens) == tokens


@pytest.mark.property
@settings(max_examples=200, deadline=None)
@given(words=_words, size_overlap=_size_and_overlap())
async def test_chunk_default_metadata_still_complete(
    words: list[str], size_overlap: tuple[int, int]
) -> None:
    """Sans métadonnées fournies, chaque chunk porte les cinq clés (à ``None``)."""
    size_tokens, overlap_tokens = size_overlap
    text = " ".join(words)

    chunks = Chunker().chunk(text, size_tokens=size_tokens, overlap_tokens=overlap_tokens)

    for chunk in chunks:
        as_dict = chunk.metadata.as_dict()
        # Les cinq clés de métadonnées sont toujours présentes (Exigence 11.5).
        assert set(as_dict) == {
            "source_id",
            "document_id",
            "page",
            "section",
            "publication_date",
        }
