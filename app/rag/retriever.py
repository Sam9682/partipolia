"""Recherche hybride lexicale + sémantique (Exigence 12.1).

Ce module implémente :class:`HybridSearch`, qui combine deux stratégies de
récupération sur ``document_chunks`` et restitue une liste de
:class:`~app.rag.types.Candidate` porteurs de leurs **sous-scores normalisés**
(prêts pour :class:`~app.rag.ranking.ScoreFusion`) :

* **Lexicale** — recherche plein-texte PostgreSQL via ``to_tsvector`` /
  ``plainto_tsquery`` ; la pertinence est mesurée par ``ts_rank_cd`` (Exigence
  12.1).
* **Sémantique** — plus proches voisins par **distance cosinus** pgvector
  (opérateur ``<=>`` sur la colonne ``embedding`` indexée HNSW) ; le vecteur de
  la requête est obtenu via l'``EmbeddingProvider`` injecté (Exigences 12.1,
  16.2).

Les deux ensembles de résultats sont **fusionnés par ``chunk_id``** : un fragment
retrouvé par les deux voies conserve ses deux sous-scores. Les sous-scores
``semantic`` et ``lexical`` sont normalisés dans ``[0, 1]`` (1 = meilleur). Les
sous-scores ``source_quality`` et ``recency`` sont dérivés des métadonnées de la
Source rattachée (vérification/type et récence de publication), également dans
``[0, 1]``. La combinaison de ces quatre sous-scores en un score unique est du
ressort de :class:`~app.rag.ranking.ScoreFusion` (Exigence 12.2).

La requête est **paramétrée** (aucune interpolation de la saisie utilisateur dans
le SQL) afin de prévenir toute injection.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Final

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.providers.base import EmbeddingProvider
from app.rag.types import Candidate

# Fenêtre de récence : une publication de plus de ``_RECENCY_HORIZON_DAYS`` jours
# obtient une récence nulle ; une publication du jour obtient 1.0 (décroissance
# linéaire). Horizon volontairement large (10 ans) adapté aux Sources politiques.
_RECENCY_HORIZON_DAYS: Final = 3650

# Qualité de Source par défaut lorsque les métadonnées sont absentes.
_DEFAULT_SOURCE_QUALITY: Final = 0.5

# Bonus de qualité selon le type de Source (Exigence 10.2). Les Sources
# officielles/législatives/académiques sont jugées de meilleure qualité.
_SOURCE_TYPE_QUALITY: Final[Mapping[str, float]] = {
    "OFFICIAL": 1.0,
    "LEGISLATION": 1.0,
    "ACADEMIC": 0.9,
    "STATISTICAL": 0.9,
    "REPORT": 0.7,
    "MEDIA": 0.5,
    "OTHER": 0.4,
}


def _clamp01(value: float) -> float:
    """Borne ``value`` dans l'intervalle ``[0, 1]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _normalize_scores(raw: Mapping[int, float]) -> dict[int, float]:
    """Normalise des scores bruts positifs dans ``[0, 1]`` par mise à l'échelle max.

    Le meilleur score brut devient 1.0 ; les autres sont proportionnels. Un
    ensemble vide ou entièrement nul produit des scores nuls. On évite ainsi de
    dépendre d'une échelle absolue (``ts_rank_cd`` et ``1 - distance`` n'ont pas
    la même dynamique) tout en préservant l'ordre relatif au sein de chaque voie.
    """
    if not raw:
        return {}
    top = max(raw.values())
    if top <= 0.0:
        return {chunk_id: 0.0 for chunk_id in raw}
    return {chunk_id: _clamp01(value / top) for chunk_id, value in raw.items()}


def _recency_score(publication_date: object) -> float:
    """Calcule une récence dans ``[0, 1]`` à partir d'une date de publication.

    Décroissance linéaire de 1.0 (aujourd'hui) à 0.0
    (``_RECENCY_HORIZON_DAYS`` jours ou plus). Une date absente ou illisible
    donne 0.0 (on ne récompense pas l'inconnu).
    """
    parsed: date | None = None
    if isinstance(publication_date, date):
        parsed = publication_date
    elif isinstance(publication_date, str) and publication_date:
        try:
            parsed = date.fromisoformat(publication_date[:10])
        except ValueError:
            parsed = None
    if parsed is None:
        return 0.0

    age_days = (date.today() - parsed).days
    if age_days <= 0:
        return 1.0
    if age_days >= _RECENCY_HORIZON_DAYS:
        return 0.0
    return _clamp01(1.0 - age_days / _RECENCY_HORIZON_DAYS)


def _source_quality_score(source_type: object, is_verified: object) -> float:
    """Dérive une qualité de Source dans ``[0, 1]`` (type + vérification).

    Base issue de :data:`_SOURCE_TYPE_QUALITY` selon le ``source_type`` ; une
    Source **non vérifiée** est pénalisée (facteur 0.8) car moins fiable
    (Exigence 10.1). Métadonnées absentes ⇒ :data:`_DEFAULT_SOURCE_QUALITY`.
    """
    if not isinstance(source_type, str) or source_type not in _SOURCE_TYPE_QUALITY:
        base = _DEFAULT_SOURCE_QUALITY
    else:
        base = _SOURCE_TYPE_QUALITY[source_type]
    if is_verified is False:
        base *= 0.8
    return _clamp01(base)


class HybridSearch:
    """Recherche hybride lexicale + sémantique sur ``document_chunks`` (Exigence 12.1).

    Le Service est instancié par requête avec une ``AsyncSession`` et un
    ``EmbeddingProvider`` (sélectionné par configuration, Exigence 16.3).
    """

    def __init__(
        self,
        session: AsyncSession,
        embedding_provider: EmbeddingProvider,
    ) -> None:
        self._session = session
        self._embedding_provider = embedding_provider

    async def search(self, query: str, *, top_k: int) -> list[Candidate]:
        """Retourne jusqu'à ``top_k`` :class:`Candidate` pour ``query`` (Exigence 12.1).

        Exécute la recherche lexicale (tsvector/tsquery) et la recherche
        sémantique (pgvector, distance cosinus), fusionne les fragments par
        ``chunk_id`` en conservant les sous-scores des deux voies, puis calcule
        les sous-scores ``source_quality`` et ``recency`` depuis les métadonnées.

        Une ``query`` vide (ou uniquement des blancs) ou un ``top_k`` non positif
        renvoie une liste vide. L'ordre de sortie est déterministe (``chunk_id``
        croissant) ; le classement final relève de :class:`ScoreFusion`.
        """
        if top_k <= 0 or not query or not query.strip():
            return []

        lexical_rows = await self._lexical_search(query, top_k=top_k)
        semantic_rows = await self._semantic_search(query, top_k=top_k)

        lexical_scores = _normalize_scores(
            {row["chunk_id"]: float(row["rank"]) for row in lexical_rows}
        )
        # Distance cosinus ∈ [0, 2] → similarité (1 - distance) ∈ [-1, 1], bornée.
        semantic_scores = _normalize_scores(
            {
                row["chunk_id"]: _clamp01(1.0 - float(row["distance"]))
                for row in semantic_rows
            }
        )

        # Métadonnées par fragment (contenu, source_type, is_verified, date).
        rows_by_id: dict[int, Mapping[str, Any]] = {}
        for row in semantic_rows:
            rows_by_id[row["chunk_id"]] = row
        for row in lexical_rows:
            rows_by_id.setdefault(row["chunk_id"], row)

        chunk_ids = set(lexical_scores) | set(semantic_scores)
        candidates: list[Candidate] = []
        for chunk_id in sorted(chunk_ids):
            row = rows_by_id.get(chunk_id, {})
            candidates.append(
                Candidate(
                    chunk_id=chunk_id,
                    content=str(row.get("content", "")),
                    semantic=semantic_scores.get(chunk_id, 0.0),
                    lexical=lexical_scores.get(chunk_id, 0.0),
                    source_quality=_source_quality_score(
                        row.get("source_type"), row.get("is_verified")
                    ),
                    recency=_recency_score(row.get("publication_date")),
                    metadata={
                        "document_id": row.get("document_id"),
                        "source_id": row.get("source_id"),
                        "source_type": row.get("source_type"),
                        "publication_date": _iso_or_none(row.get("publication_date")),
                    },
                )
            )
        return candidates

    # ------------------------------------------------------------------ #
    # Recherche lexicale plein-texte (tsvector/tsquery) — Exigence 12.1   #
    # ------------------------------------------------------------------ #
    async def _lexical_search(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Exécute la recherche plein-texte PostgreSQL et renvoie les lignes brutes.

        ``plainto_tsquery`` transforme la saisie utilisateur libre en requête
        plein-texte (paramétrée, donc sûre) ; ``ts_rank_cd`` mesure la
        pertinence. Jointure sur ``documents`` et ``sources`` pour disposer des
        métadonnées de qualité/récence.
        """
        statement = text(
            """
            SELECT
                c.id AS chunk_id,
                c.content AS content,
                c.document_id AS document_id,
                d.source_id AS source_id,
                s.source_type AS source_type,
                s.is_verified AS is_verified,
                s.publication_date AS publication_date,
                ts_rank_cd(
                    to_tsvector('french', c.content),
                    plainto_tsquery('french', :query)
                ) AS rank
            FROM document_chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            JOIN sources AS s ON s.id = d.source_id
            WHERE to_tsvector('french', c.content)
                  @@ plainto_tsquery('french', :query)
            ORDER BY rank DESC
            LIMIT :top_k
            """
        )
        result = await self._session.execute(
            statement, {"query": query, "top_k": top_k}
        )
        return [dict(row) for row in result.mappings().all()]

    # ------------------------------------------------------------------ #
    # Recherche sémantique pgvector (distance cosinus) — Exigence 12.1    #
    # ------------------------------------------------------------------ #
    async def _semantic_search(self, query: str, *, top_k: int) -> list[dict[str, Any]]:
        """Exécute la recherche des plus proches voisins par distance cosinus.

        Le vecteur de la requête est produit par l'``EmbeddingProvider`` puis
        comparé aux ``embedding`` par l'opérateur ``<=>`` (distance cosinus
        pgvector). Les fragments sans embedding sont ignorés.
        """
        embeddings = self._embedding_provider.embed([query])
        if not embeddings or not embeddings[0]:
            return []
        query_vector = list(embeddings[0])

        statement = text(
            """
            SELECT
                c.id AS chunk_id,
                c.content AS content,
                c.document_id AS document_id,
                d.source_id AS source_id,
                s.source_type AS source_type,
                s.is_verified AS is_verified,
                s.publication_date AS publication_date,
                (c.embedding <=> :query_vector) AS distance
            FROM document_chunks AS c
            JOIN documents AS d ON d.id = c.document_id
            JOIN sources AS s ON s.id = d.source_id
            WHERE c.embedding IS NOT NULL
            ORDER BY c.embedding <=> :query_vector
            LIMIT :top_k
            """
        ).bindparams(bindparam("query_vector", value=query_vector))
        result = await self._session.execute(statement, {"top_k": top_k})
        return [dict(row) for row in result.mappings().all()]


def _iso_or_none(value: object) -> str | None:
    """Sérialise une date/datetime en ISO, sinon renvoie la valeur telle quelle."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, str):
        return value
    return None


__all__ = ["HybridSearch"]
