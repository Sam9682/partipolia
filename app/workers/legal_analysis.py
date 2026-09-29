"""Tâche Celery de génération asynchrone des analyses « Réparer la loi ».

Ce module fournit la tâche ``analyze_reform`` (Exigences 3.1, 5.1, 6.1, 7.1,
12.1, 12.6) qui **régénère hors-ligne** les 9 Agents_IA_Contradictoires d'une
Réforme_Proposée, matérialise leurs analyses dans ``legal_analyses`` et journalise
l'audit — le tout **hors du chemin de requête FastAPI** (Exigence 12.1 / 23.3).

Déclencheurs (émis par l'API sans jamais exécuter la génération en ligne) :

* création d'une réforme (Réforme_Proposée nouvellement enregistrée) ;
* rattachement d'une Source à la réforme (le corpus documentaire change) ;
* expiration du TTL de fraîcheur d'une analyse (rafraîchissement périodique).

Découpage (aligné sur :mod:`app.workers.statistics` et :mod:`app.workers.ingestion`) :

* La **logique métier** (récupération obligatoire avant génération, validation des
  citations, UPSERT dans ``legal_analyses`` et journalisation ``audit_service``
  via l'action ``RAG_ANALYSIS_PRODUCED``) reste entièrement dans
  :meth:`~app.services.legal_analysis_service.LegalAnalysisService.run_reform_agents` ;
  la tâche n'orchestre que l'ouverture d'une session, l'assemblage des
  dépendances (``RagPipeline`` + ``AuditService``) et l'appel du Service.
* La **récupération est obligatoire avant génération** : elle est garantie par le
  ``RagPipeline`` lui-même, qui n'appelle le LLM que si la récupération est non
  vide ; un agent sans Source récupérée est matérialisé ``INDISPONIBLE`` sans
  génération conservée (Exigences 3.5, 7.10, 12.1).
* La journalisation structurée (``request_id``) est importée de façon **différée**
  et son échec toléré, afin que le module reste importable dans un environnement
  de test hors-ligne dépourvu de la couche d'observabilité.

Le résultat retourné est un dictionnaire sérialisable JSON (le
:class:`~app.schemas.legal_problem.ReformAnalysisBundle` régénéré), adapté au
back-end de résultats Redis.
"""

from __future__ import annotations

from typing import Any, Literal


class _NullLogger:
    """Logger de repli sans effet (cf. :mod:`app.workers.statistics`)."""

    def __getattr__(self, _name: str) -> Any:
        def _noop(*_args: object, **_kwargs: object) -> None:
            return None

        return _noop


class _null_context:
    """Context manager sans effet (repli de ``bind_request_id``)."""

    def __enter__(self) -> str:
        return ""

    def __exit__(self, *_exc: object) -> Literal[False]:
        return False


def _log() -> Any:
    """Retourne le logger structuré (import différé, échec toléré)."""
    try:
        from app.core.logging import get_logger
    except Exception:  # pragma: no cover - couche logging indisponible
        return _NullLogger()
    return get_logger(__name__)


def _bind_request_id() -> Any:
    """Context manager de corrélation (import différé, échec toléré)."""
    try:
        from app.core.logging import bind_request_id
    except Exception:  # pragma: no cover - couche logging indisponible
        return _null_context()
    return bind_request_id()


async def _run_analyze_reform(reform_id: int) -> dict[str, Any]:
    """Ouvre une session, régénère les 9 agents et sérialise le bundle.

    Fonction ``async`` dédiée exécutée par ``asyncio.run`` depuis le corps
    synchrone de la tâche Celery. L'assemblage du :class:`RagPipeline` reprend
    celui du chemin HTTP (``app.api.v1.chat.get_rag_pipeline``) : recherche hybride
    adossée à la session + provider d'embeddings, génération via le ``LLMProvider``
    configuré à faible température (Exigence 12.2). La persistance des
    ``legal_analyses`` et la journalisation d'audit sont assurées par le Service
    lui-même (``run_reform_agents``) ; la tâche se borne à valider la transaction.
    """
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.rag.pipeline import RagPipeline
    from app.rag.providers import get_embedding_provider, get_llm_provider
    from app.rag.retriever import HybridSearch
    from app.services.audit_service import AuditService
    from app.services.legal_analysis_service import LegalAnalysisService

    async with SessionLocal() as session:
        try:
            pipeline = RagPipeline(
                hybrid_search=HybridSearch(session, get_embedding_provider()),
                llm_provider=get_llm_provider(),
                temperature=settings.llm_temperature,
                top_k=settings.rag_top_k,
                keep=settings.rag_rerank_keep,
            )
            service = LegalAnalysisService(session, pipeline, AuditService(session))
            bundle = await service.run_reform_agents(reform_id)
            await session.commit()
            return bundle.model_dump(mode="json")
        except Exception:
            await session.rollback()
            raise


# Paramètres de réessai communs (Exigence 23 — backoff exponentiel), alignés sur
# les tâches d'ingestion et de statistiques.
_RETRY_KWARGS: dict[str, Any] = {
    "autoretry_for": (Exception,),
    "retry_backoff": True,
    "retry_backoff_max": 600,
    "retry_jitter": True,
    "max_retries": 5,
}


# Import de l'application Celery après les définitions (évite les cycles et garde
# la logique importable même sans Celery configuré).
from app.workers.celery_app import celery_app  # noqa: E402


@celery_app.task(  # type: ignore[untyped-decorator]  # décorateur Celery non typé
    name="app.workers.legal_analysis.analyze_reform", **_RETRY_KWARGS
)
def analyze_reform(reform_id: int) -> dict[str, Any]:
    """Régénère (hors-ligne) les analyses IA d'une Réforme_Proposée.

    Émise par l'API à la création d'une réforme, au rattachement d'une Source ou à
    l'expiration du TTL de fraîcheur, puis exécutée par le Worker_Celery hors du
    chemin de requête (Exigences 12.1, 23.3). Délègue à
    :meth:`LegalAnalysisService.run_reform_agents` — **récupération obligatoire
    avant génération**, UPSERT des ``legal_analyses`` et journalisation d'audit
    (Exigences 3.1, 5.1, 6.1, 7.1, 12.6). Retourne le bundle régénéré (JSON) ;
    réessaie avec backoff exponentiel en cas d'erreur transitoire (Provider, base).
    """
    import asyncio

    with _bind_request_id():
        _log().info("task_analyze_reform_start", reform_id=reform_id)
        result = asyncio.run(_run_analyze_reform(reform_id))
        _log().info("task_analyze_reform_completed", reform_id=reform_id)
        return result


__all__ = ["analyze_reform"]
