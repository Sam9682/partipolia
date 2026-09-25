# Feature: partipolia-platform, Property 10: Découplage IA — non-écriture dans les tables de décision
"""Test de propriété du découplage IA (Exigences 15, 18, 19).

**Property 10: Découplage IA — non-écriture dans les tables de décision**

**Validates: Requirements 15, 18, 19**

_Pour toute_ question et _pour tout_ contexte documentaire, l'exécution du
:class:`~app.rag.pipeline.RagPipeline` (et de la brique de récupération réelle
:class:`~app.rag.retriever.HybridSearch` qu'il pilote) n'émet **aucune** écriture
(``INSERT`` / ``UPDATE`` / ``DELETE``) vers les tables de décision ``votes`` ou
``program_proposals`` : l'IA **documente et assiste** mais ne vote pas et ne
décide pas du Programme.

Méthode — un **espion de session/store** (:class:`_SpySession`) enregistre chaque
instruction SQL exécutée par le pipeline. Comme toute interaction avec la base
transite par ``AsyncSession.execute``, il suffit de vérifier qu'aucune des
instructions capturées n'est une écriture ciblant les tables de décision. Les
Providers IA sont **simulés** (LLM et Embedding), de sorte que le test s'exécute
hors-ligne et n'exerce que le vrai chemin en lecture seule du pipeline.
"""

from __future__ import annotations

import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.pipeline import RagPipeline
from app.rag.retriever import HybridSearch

# Tables de décision : le pipeline IA ne doit jamais y écrire (Property 10).
_DECISION_TABLES = ("votes", "program_proposals")

# Verbes d'écriture SQL considérés comme des mutations de la base.
_WRITE_VERBS = ("insert", "update", "delete")


class _RecordingResult:
    """Résultat minimal exposant ``mappings().all()`` comme SQLAlchemy.

    Le pipeline lit les lignes via ``result.mappings().all()`` ; on renvoie les
    lignes prédéfinies (fragments simulés) sans base réelle.
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows

    def mappings(self) -> "_RecordingResult":
        return self

    def all(self) -> list[dict[str, object]]:
        return list(self._rows)


class _SpySession:
    """``AsyncSession`` espionne — enregistre chaque SQL exécuté, n'écrit jamais.

    Toute requête du pipeline transite par ``execute`` : on capture le texte SQL
    (compilé) pour l'auditer. Les recherches lexicale/sémantique renvoient les
    lignes simulées ; toute autre requête renvoie un résultat vide. Aucune
    méthode d'écriture (``add`` / ``commit`` / ``flush``) n'est fournie : leur
    éventuel appel lèverait ``AttributeError`` et ferait échouer le test.
    """

    def __init__(self, rows: list[dict[str, object]]) -> None:
        self._rows = rows
        self.executed_sql: list[str] = []

    async def execute(self, statement, params=None):  # noqa: ANN001 - signature SQLAlchemy
        sql = str(statement).lower()
        self.executed_sql.append(sql)
        # Les deux voies de recherche hybride sélectionnent des fragments.
        if "from document_chunks" in sql:
            return _RecordingResult(self._rows)
        return _RecordingResult([])


class _FakeEmbeddingProvider:
    """``EmbeddingProvider`` simulé : vecteur déterministe non nul, sans réseau."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3] for _ in texts]


class _RecordingLLM:
    """``LLMProvider`` simulé : réponse fixe citée, aucun appel externe."""

    def __init__(self) -> None:
        self.calls = 0

    def generate(self, messages, temperature: float) -> str:  # noqa: ANN001
        self.calls += 1
        return "Voici les arguments documentés [1]."


def _chunk_rows(count: int) -> list[dict[str, object]]:
    """Construit ``count`` lignes de fragments simulées (métadonnées minimales)."""
    return [
        {
            "chunk_id": i + 1,
            "content": f"Passage documentaire numéro {i + 1}.",
            "document_id": 200 + i,
            "source_id": 100 + i,
            "source_type": "OFFICIAL",
            "is_verified": True,
            "publication_date": "2024-01-01",
            "rank": 1.0 / (i + 1),
            "distance": 0.1 * (i + 1),
        }
        for i in range(count)
    ]


def _is_forbidden_write(sql: str) -> bool:
    """Vrai si ``sql`` est une écriture ciblant une table de décision."""
    lowered = sql.lower()
    if not any(re.search(rf"\b{verb}\b", lowered) for verb in _WRITE_VERBS):
        return False
    return any(table in lowered for table in _DECISION_TABLES)


# Questions arbitraires (y compris sensibles/comparatives) et contextes variés :
# on couvre « pour toute question et pour tout contexte » (Property 10).
_questions = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=120,
)
_proposal_ids = st.one_of(st.none(), st.integers(min_value=1, max_value=10_000))
_context_sizes = st.integers(min_value=0, max_value=8)


@pytest.mark.rag
@pytest.mark.property
@settings(max_examples=150)
@given(message=_questions, proposal_id=_proposal_ids, context_size=_context_sizes)
async def test_rag_pipeline_never_writes_to_decision_tables(
    message: str, proposal_id: int | None, context_size: int
) -> None:
    """Le pipeline n'émet aucune écriture vers ``votes``/``program_proposals``.

    Pour toute question et tout contexte (nombre de fragments récupérés variable,
    y compris zéro), on exécute le vrai pipeline avec la vraie recherche hybride
    branchée sur une session espionne. On vérifie ensuite qu'aucune instruction
    SQL capturée n'est une écriture ciblant une table de décision.
    """
    session = _SpySession(_chunk_rows(context_size))
    hybrid_search = HybridSearch(session, _FakeEmbeddingProvider())  # type: ignore[arg-type]
    llm = _RecordingLLM()
    pipeline = RagPipeline(hybrid_search=hybrid_search, llm_provider=llm)

    await pipeline.answer(message, proposal_id=proposal_id)

    # Invariant central : aucune écriture vers les tables de décision.
    forbidden = [sql for sql in session.executed_sql if _is_forbidden_write(sql)]
    assert forbidden == [], (
        "Le pipeline IA a émis une écriture interdite vers une table de "
        f"décision : {forbidden!r}"
    )

    # Renfort : toute instruction SQL exécutée est une lecture (SELECT).
    for sql in session.executed_sql:
        assert not any(
            re.search(rf"\b{verb}\b", sql) for verb in _WRITE_VERBS
        ), f"Instruction non-lecture inattendue exécutée par le pipeline : {sql!r}"


@pytest.mark.rag
@pytest.mark.property
@settings(max_examples=150)
@given(message=_questions, context_size=st.integers(min_value=1, max_value=8))
async def test_rag_pipeline_only_reads_document_chunks(
    message: str, context_size: int
) -> None:
    """Quand des passages existent, les seules requêtes touchent ``document_chunks``.

    Confirme le découplage : le pipeline consomme la base uniquement pour la
    recherche hybride (lecture de ``document_chunks``) et ne touche jamais les
    tables de décision, ni en lecture ni en écriture.
    """
    session = _SpySession(_chunk_rows(context_size))
    hybrid_search = HybridSearch(session, _FakeEmbeddingProvider())  # type: ignore[arg-type]
    pipeline = RagPipeline(hybrid_search=hybrid_search, llm_provider=_RecordingLLM())

    await pipeline.answer(message, proposal_id=None)

    assert session.executed_sql, "La recherche hybride aurait dû interroger la base."
    for sql in session.executed_sql:
        assert "from document_chunks" in sql
        for table in _DECISION_TABLES:
            assert table not in sql


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
