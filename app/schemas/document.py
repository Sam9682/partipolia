"""Schémas Pydantic v2 pour l'ingestion documentaire (Exigences 11, 23).

Ces schémas définissent le contrat d'entrée/sortie du point d'accès
``POST /api/v1/admin/documents/ingest`` (``app/api/v1/documents.py``) :

* :class:`DocumentIngestRequest` — corps de la requête : l'``url`` du Document à
  ingérer et l'``source_id`` de la Source à laquelle il se rattache
  (Exigence 11.1) ;
* :class:`DocumentIngestAccepted` — réponse ``202 Accepted`` confirmant que la
  tâche Celery ``ingest_document`` a été **émise** (``.delay(...)``) sans bloquer
  l'API (Exigence 23.3). Elle porte l'identifiant de tâche Celery afin de
  permettre un suivi ultérieur du travail asynchrone.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class DocumentIngestRequest(BaseModel):
    """Corps de ``POST /api/v1/admin/documents/ingest`` (Exigences 11.1, 23.2).

    Décrit un Document à ingérer : son ``url`` (téléchargée par le
    Pipeline_D_Ingestion) et l'``source_id`` de la Source de rattachement. Une
    ``url`` vide ou un ``source_id`` non strictement positif entraîne une erreur
    de validation Pydantic (``422``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    url: str = Field(min_length=1, max_length=2048)
    source_id: int = Field(gt=0)


class DocumentIngestAccepted(BaseModel):
    """Réponse ``202 Accepted`` d'un déclenchement d'ingestion (Exigence 23.3).

    L'API se contente d'émettre la tâche Celery ``ingest_document`` (``.delay``)
    puis retourne immédiatement : le travail long s'exécute dans le Worker_Celery,
    l'API n'est jamais bloquée. ``task_id`` identifie la tâche émise (suivi).
    """

    status: str = "accepted"
    task_id: str | None = None
    url: str
    source_id: int
