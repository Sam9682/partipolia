"""API REST de l'Assistant IA documentaire — endpoint chat RAG (Exigences 13, 14, 30).

Point d'accès :

* ``POST /api/v1/chat`` — interroge l'Assistant IA avec ``{message, proposal_id}``
  et renvoie ``{answer, sources[], confidence}`` (Exigence 13.1). **Protégé** : la
  garde :func:`app.api.deps.get_current_user` réserve l'accès aux Utilisateurs
  authentifiés (``401`` sinon) conformément au tableau REST de la conception.

Le traitement est délégué au :class:`~app.rag.pipeline.RagPipeline`, assemblé par
requête à partir de la :class:`~app.rag.retriever.HybridSearch` (session +
``EmbeddingProvider``) et du ``LLMProvider`` (tous deux sélectionnés par
configuration — Exigence 16.3). Le pipeline reste en lecture seule côté domaine
(Property 10) : il n'écrit jamais dans ``votes`` ni ``program_proposals``.

**Journalisation RAG (Exigences 14.7, 30.2)** : chaque réponse est tracée dans le
journal structuré avec ``request_id`` (issu du contexte de requête), ``user_id``,
``timestamp``, le ``model`` de génération configuré et la liste
``retrieved_documents`` (identifiants des Documents/fragments cités), afin de
rendre toute requête IA retrouvable a posteriori (Exigence 30.2). Le contenu de la
question et de la réponse n'est pas journalisé ici (politique de rétention
séparée — Exigence 30.3).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_session
from app.core.rate_limit import chat_rate_limit
from app.core.logging import get_logger, get_request_id
from app.rag.pipeline import RagPipeline
from app.rag.providers import get_embedding_provider, get_llm_provider
from app.rag.retriever import HybridSearch
from app.schemas.auth import UserPublic
from app.schemas.chat import ChatRequest, ChatResponse

router = APIRouter()

_log = get_logger(__name__, component="rag")


def get_rag_pipeline(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> RagPipeline:
    """Assemble un :class:`RagPipeline` lié à la session de la requête (Exigence 13.3).

    La recherche hybride est adossée à la session courante et à
    l'``EmbeddingProvider`` configuré ; la génération utilise le ``LLMProvider``
    configuré à faible température (``settings.llm_temperature`` — Exigence 14.2).
    Les fournisseurs sont sélectionnés par configuration (Exigence 16.3) et
    injectés pour rester testables hors-ligne.
    """
    hybrid_search = HybridSearch(session, get_embedding_provider())
    return RagPipeline(
        hybrid_search=hybrid_search,
        llm_provider=get_llm_provider(),
        temperature=settings.llm_temperature,
        top_k=settings.rag_top_k,
        keep=settings.rag_rerank_keep,
    )


@router.post(
    "",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Interroger l'Assistant IA documentaire (RAG)",
    # Protection de débit **renforcée** du chat (Exigence 26.5) : seuil abaissé
    # dédié, en-tête ``Retry-After`` sur ``429``.
    dependencies=[Depends(chat_rate_limit)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            "description": "Corps invalide : champs en erreur renvoyés"
        },
        status.HTTP_429_TOO_MANY_REQUESTS: {
            "description": "Seuil de débit dépassé (protection renforcée du chat)"
        },
    },
)
async def chat(
    pipeline: Annotated[RagPipeline, Depends(get_rag_pipeline)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    data: ChatRequest,
) -> ChatResponse:
    """Répond à une question documentée via le Pipeline RAG (Exigences 13.1, 13.3).

    Réservé aux Utilisateurs authentifiés (``401`` sinon). Renvoie
    ``{answer, sources[], confidence}`` (Exigence 13.1) ; en l'absence de passage
    pertinent, ``answer`` indique explicitement l'indisponibilité, ``sources`` est
    vide et ``confidence`` vaut ``0.0`` (Exigences 13.8, 14.6).

    Chaque réponse est journalisée (request_id, user_id, timestamp, modèle,
    documents récupérés) pour l'observabilité (Exigences 14.7, 30.2).
    """
    response = await pipeline.answer(
        data.message,
        data.proposal_id,
        user=current_user,
    )

    _log_rag_response(current_user, data, response)
    return response


def _log_rag_response(
    user: UserPublic, request: ChatRequest, response: ChatResponse
) -> None:
    """Journalise une réponse RAG de façon retrouvable (Exigences 14.7, 30.2).

    Trace ``request_id`` (contexte), ``user_id``, ``timestamp`` (UTC ISO 8601),
    ``model`` de génération configuré et ``retrieved_documents`` (identifiants des
    Documents effectivement cités dans la réponse). Le contenu de la question et
    de la réponse n'est pas journalisé (Exigence 30.3).
    """
    retrieved_documents = sorted(
        {
            source.document_id
            for source in response.sources
            if source.document_id is not None
        }
    )
    _log.info(
        "rag_response",
        request_id=get_request_id(),
        user_id=user.id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        model=settings.llm_model,
        proposal_id=request.proposal_id,
        retrieved_documents=retrieved_documents,
        source_count=len(response.sources),
        confidence=response.confidence,
    )


__all__ = ["router", "get_rag_pipeline", "chat"]
