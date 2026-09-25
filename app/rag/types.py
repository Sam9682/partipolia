"""Types partagés du pipeline de recherche/fusion RAG (Exigence 12).

Regroupe les structures de données échangées entre les étapes de récupération et
de classement afin d'éviter les imports circulaires entre modules
(``retriever`` produit des :class:`Candidate`, consommés par ``ranking`` qui
émet des :class:`ScoredChunk`).

Séparation des rôles :

* :class:`Candidate` — un fragment (``document_chunks``) retrouvé par la
  :class:`~app.rag.retriever.HybridSearch`, porteur de ses **sous-scores**
  normalisés dans ``[0, 1]`` : ``semantic``, ``lexical``, ``source_quality`` et
  ``recency`` (Exigence 12.1, 12.2).
* :class:`ScoredChunk` — un candidat auquel la
  :class:`~app.rag.ranking.ScoreFusion` a associé un ``score`` fusionné, prêt
  pour le reranking puis la construction de contexte (Exigence 12.2, 12.3).

Les deux structures sont immuables (``frozen``) : chaque étape produit de
nouvelles valeurs plutôt que de muter son entrée.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    """Fragment retrouvé par la recherche hybride, avec ses sous-scores (Exigence 12).

    Les quatre sous-scores sont **normalisés dans ``[0, 1]``** (1 = meilleur) afin
    que la fusion produise une combinaison convexe (Property 8) :

    * ``semantic`` — proximité sémantique (dérivée de la distance cosinus
      pgvector : ``1 - distance``) ;
    * ``lexical`` — pertinence lexicale plein-texte PostgreSQL
      (``ts_rank``/``ts_rank_cd`` normalisé) ;
    * ``source_quality`` — qualité de la Source (vérification, type) ;
    * ``recency`` — récence de la publication (1 = récent).

    ``content`` porte le texte du fragment et ``metadata`` ses métadonnées
    (source_id, document_id, page, section, publication_date — Exigence 11.5).
    """

    chunk_id: int
    content: str
    semantic: float = 0.0
    lexical: float = 0.0
    source_quality: float = 0.0
    recency: float = 0.0
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """Candidat auquel un ``score`` fusionné a été attribué (Exigence 12.2).

    Conserve une référence vers le :class:`Candidate` d'origine (et donc ses
    sous-scores et métadonnées) afin que les étapes ultérieures — reranking
    (Exigence 12.3), construction de contexte et citations (Exigence 13.4) —
    disposent de toute l'information.
    """

    candidate: Candidate
    score: float

    @property
    def chunk_id(self) -> int:
        """Identifiant du fragment sous-jacent (raccourci de lecture)."""
        return self.candidate.chunk_id

    @property
    def content(self) -> str:
        """Texte du fragment sous-jacent (raccourci de lecture)."""
        return self.candidate.content


@dataclass(frozen=True, slots=True)
class Citation:
    """Citation numérotée rattachée à un fragment documentaire (Exigence 13.4).

    Produite par :class:`~app.rag.context.ContextBuilder` lors de la construction
    du contexte : chaque passage retenu reçoit un ``number`` (1-indexé, croissant
    et sans trou) qui apparaît dans le contexte transmis au LLM sous la forme
    ``[n]`` et dans la liste des Sources renvoyée à l'Utilisateur.

    * ``number`` — rang d'affichage de la citation (≥ 1) ;
    * ``chunk_id`` — fragment (``document_chunks``) source de la citation ;
    * ``content`` — texte du passage cité (repris tel quel du fragment) ;
    * ``source_id`` / ``document_id`` — identifiants d'origine (métadonnées),
      utiles à l'affichage des Sources et à la validation d'exactitude ;
    * ``metadata`` — métadonnées additionnelles du fragment (type de Source,
      date de publication, etc.), reprises du :class:`Candidate`.
    """

    number: int
    chunk_id: int
    content: str
    source_id: int | None = None
    document_id: int | None = None
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Résultat de la validation des citations d'une réponse (Exigences 14.3–14.5).

    Émis par :class:`~app.rag.context.CitationValidator` : indique si la réponse
    est entièrement étayée par les citations disponibles et, sinon, liste les
    affirmations factuelles détectées comme **non sourcées**.

    * ``is_valid`` — vrai si aucune affirmation factuelle non sourcée n'a été
      détectée (Exigence 14.5) ;
    * ``unsourced_claims`` — affirmations factuelles importantes sans marqueur de
      citation valide (``[n]``) ;
    * ``cited_numbers`` — numéros de citation ``[n]`` effectivement référencés par
      la réponse (déduplication, ordre croissant) ;
    * ``invalid_citation_numbers`` — numéros ``[n]`` cités par la réponse mais
      absents des citations fournies (citation fabriquée / hors périmètre).
    """

    is_valid: bool
    unsourced_claims: tuple[str, ...] = ()
    cited_numbers: tuple[int, ...] = ()
    invalid_citation_numbers: tuple[int, ...] = ()


__all__ = ["Candidate", "ScoredChunk", "Citation", "ValidationResult"]
