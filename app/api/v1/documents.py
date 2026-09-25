"""API REST d'ingestion documentaire réservée à l'Administrateur (Exigences 11, 23).

Point d'accès :

* ``POST /api/v1/admin/documents/ingest`` — déclenche l'ingestion d'un Document
  depuis son URL. Réservé à l'Administrateur (:func:`app.api.deps.require_admin`).

Principe directeur (Exigence 23.3 — « ne pas bloquer l'API ») : l'endpoint se
contente d'**émettre** la tâche Celery ``ingest_document`` via ``.delay(...)`` et
retourne immédiatement un ``202 Accepted``. Tout le travail long (téléchargement,
extraction, chunking, embeddings, écriture pgvector) se déroule dans le
Worker_Celery, hors du chemin de requête FastAPI (Exigences 11.1, 23.1, 23.2).

L'émetteur de tâche est isolé derrière la dépendance :func:`get_task_dispatcher`
afin de garder ce module importable et testable hors-ligne : les tests
surchargent la dépendance par un émetteur factice, sans broker Redis ni Celery
réels.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, status

from app.api.deps import require_admin
from app.schemas.auth import UserPublic
from app.schemas.document import DocumentIngestAccepted, DocumentIngestRequest

router = APIRouter()


class TaskDispatcher(Protocol):
    """Port d'émission de la tâche d'ingestion (isole Celery de l'API).

    Le contrat imite l'appel ``ingest_document.delay(url, source_id)`` : il émet
    la tâche sans l'exécuter et retourne un identifiant de tâche (ou ``None`` si
    le back-end ne fournit pas d'``id``).
    """

    def __call__(self, url: str, source_id: int) -> str | None:
        ...


def _dispatch_ingest_document(url: str, source_id: int) -> str | None:
    """Émet la tâche Celery ``ingest_document`` sans bloquer l'API (Exigence 23.3).

    L'import de la tâche est **différé** au moment de l'appel afin que ce module
    reste importable dans un environnement de test hors-ligne dépourvu de Celery
    ou d'un broker Redis configuré. En production, ``.delay(...)`` place le
    message sur le broker et rend la main immédiatement.
    """
    from app.workers.ingestion import ingest_document

    async_result = ingest_document.delay(url, source_id)
    return getattr(async_result, "id", None)


def get_task_dispatcher() -> TaskDispatcher:
    """Dépendance fournissant l'émetteur de tâche d'ingestion.

    Surchargeable en test (``app.dependency_overrides``) par un émetteur factice
    afin de vérifier le câblage HTTP sans Celery ni Redis.
    """
    return _dispatch_ingest_document


@router.post(
    "/documents/ingest",
    response_model=DocumentIngestAccepted,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Déclencher l'ingestion d'un Document (Administrateur)",
    responses={
        status.HTTP_202_ACCEPTED: {"description": "Ingestion acceptée (tâche émise)"},
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_403_FORBIDDEN: {"description": "Action réservée à l'administration"},
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Requête invalide"},
    },
)
async def ingest_document_endpoint(
    _admin: Annotated[UserPublic, Depends(require_admin)],
    dispatch: Annotated[TaskDispatcher, Depends(get_task_dispatcher)],
    data: DocumentIngestRequest,
) -> DocumentIngestAccepted:
    """Émet la tâche Celery d'ingestion et retourne ``202`` (Exigences 11.1, 23.2, 23.3).

    Réservé à l'Administrateur. L'API n'exécute jamais le pipeline : elle place la
    tâche ``ingest_document`` sur le broker (``.delay``) et rend la main
    immédiatement, garantissant que le chemin de requête n'est pas bloqué par une
    opération longue (Exigence 23.3).
    """
    task_id = dispatch(data.url, data.source_id)
    return DocumentIngestAccepted(
        status="accepted",
        task_id=task_id,
        url=data.url,
        source_id=data.source_id,
    )
