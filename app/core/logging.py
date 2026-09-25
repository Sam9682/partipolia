"""Journalisation structurée pour PARTIPOLAI.

Ce module centralise la configuration des logs structurés (JSON) partagée par
l'API FastAPI, le Worker_Celery, la couche base de données et le Pipeline_RAG
(Exigences 30.1 et 26.3).

Chaque enregistrement de log porte un champ ``request_id`` corrélant toutes les
opérations d'une même requête. Le ``request_id`` est stocké dans une
:class:`contextvars.ContextVar`, ce qui le rend disponible de façon transparente
dans le code synchrone comme asynchrone (tâches ``asyncio`` et threads dérivés),
sans avoir à le propager explicitement d'appel en appel.

Utilisation typique ::

    from app.core.logging import configure_logging, get_logger, bind_request_id

    configure_logging()                 # une fois, au démarrage du process
    log = get_logger(__name__)

    with bind_request_id("abc-123"):
        log.info("proposal_created", proposal_id=42)
    # -> {"event": "proposal_created", "proposal_id": 42,
    #     "request_id": "abc-123", "level": "info", "timestamp": "..."}
"""

from __future__ import annotations

import logging
import os
import sys
import uuid
from collections.abc import Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

import structlog
from structlog.types import EventDict, Processor

__all__ = [
    "REQUEST_ID_HEADER",
    "bind_contextvars",
    "bind_request_id",
    "clear_request_id",
    "configure_logging",
    "generate_request_id",
    "get_logger",
    "get_request_id",
    "set_request_id",
]

# En-tête HTTP standard utilisé pour propager l'identifiant de requête
# (X-Request-ID, Exigence 26.3 / 26.6).
REQUEST_ID_HEADER = "X-Request-ID"

# Stockage contextuel du request_id courant. ``None`` signifie « hors requête »
# (par exemple une tâche Celery périodique) ; le champ est alors omis des logs.
_request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)


def generate_request_id() -> str:
    """Génère un nouvel identifiant de requête (UUID4 hexadécimal)."""
    return uuid.uuid4().hex


def set_request_id(request_id: str) -> Token[str | None]:
    """Fixe le ``request_id`` du contexte courant.

    Retourne un :class:`~contextvars.Token` permettant de restaurer la valeur
    précédente via :func:`clear_request_id`.
    """
    return _request_id_ctx.set(request_id)


def get_request_id() -> str | None:
    """Retourne le ``request_id`` du contexte courant, ou ``None`` si absent."""
    return _request_id_ctx.get()


def clear_request_id(token: Token[str | None]) -> None:
    """Restaure la valeur précédente du ``request_id`` à partir d'un token."""
    _request_id_ctx.reset(token)


@contextmanager
def bind_request_id(request_id: str | None = None) -> Iterator[str]:
    """Context manager liant un ``request_id`` pour la durée du bloc.

    Si ``request_id`` est ``None``, un nouvel identifiant est généré. La valeur
    précédente est restaurée à la sortie du bloc, y compris en cas d'exception.

    Cet outil est réutilisable par l'API (middleware), le Worker_Celery (début de
    tâche), la couche base et le Pipeline_RAG.
    """
    rid = request_id or generate_request_id()
    token = set_request_id(rid)
    try:
        yield rid
    finally:
        clear_request_id(token)


def _add_request_id(
    _logger: Any, _method_name: str, event_dict: EventDict
) -> EventDict:
    """Processor structlog injectant le ``request_id`` du contexte dans le log.

    Le champ n'est ajouté que s'il n'a pas déjà été fourni explicitement à
    l'appel de log et qu'un identifiant est présent dans le contexte.
    """
    if "request_id" not in event_dict:
        request_id = get_request_id()
        if request_id is not None:
            event_dict["request_id"] = request_id
    return event_dict


def _resolve_level(level: str | int | None) -> int:
    """Convertit un niveau de log (nom ou entier) en constante ``logging``."""
    if level is None:
        level = os.getenv("LOG_LEVEL", "INFO")
    if isinstance(level, int):
        return level
    resolved = logging.getLevelName(str(level).upper())
    # ``getLevelName`` renvoie une chaîne "Level X" pour un nom inconnu.
    return resolved if isinstance(resolved, int) else logging.INFO


def _use_json(json_logs: bool | None) -> bool:
    """Détermine le format de sortie (JSON vs. console lisible)."""
    if json_logs is not None:
        return json_logs
    fmt = os.getenv("LOG_FORMAT", "json").strip().lower()
    return fmt != "console"


def configure_logging(
    *,
    level: str | int | None = None,
    json_logs: bool | None = None,
) -> None:
    """Configure la journalisation structurée du process.

    À appeler une seule fois au démarrage de chaque process (API, Worker_Celery,
    scripts). La configuration :

    * ajoute automatiquement ``timestamp`` (ISO 8601 UTC), ``level`` et
      ``request_id`` (via le contexte) à chaque enregistrement ;
    * émet du JSON par défaut (``LOG_FORMAT=console`` bascule sur un rendu lisible
      pour le développement) ;
    * relaie les logs de la ``logging`` standard (uvicorn, SQLAlchemy, Celery,
      httpx) à travers la même chaîne de traitement structlog.

    :param level: niveau de log (nom ou entier). Défaut : ``$LOG_LEVEL`` ou INFO.
    :param json_logs: forcer (``True``) ou désactiver (``False``) la sortie JSON.
        Défaut : ``$LOG_FORMAT`` (``json`` sauf ``console``).
    """
    log_level = _resolve_level(level)
    render_json = _use_json(json_logs)

    # Processors communs, appliqués aussi bien aux logs structlog qu'aux logs
    # provenant de la ``logging`` standard.
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_request_id,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    final_renderer: Processor = (
        structlog.processors.JSONRenderer()
        if render_json
        else structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())
    )

    structlog.configure(
        processors=[
            *shared_processors,
            # Prépare l'event dict pour un formatter stdlib partagé.
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Fait transiter les logs de la bibliothèque standard par structlog afin
    # d'homogénéiser le format (JSON + request_id) pour toutes les sources.
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            final_renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # Évite les doubles émissions et laisse la config racine gérer les loggers
    # tiers bruyants (uvicorn.access notamment).
    for noisy in ("uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error"):
        std_logger = logging.getLogger(noisy)
        std_logger.handlers.clear()
        std_logger.propagate = True


def get_logger(name: str | None = None, **initial_values: Any) -> Any:
    """Retourne un logger structlog lié, éventuellement pré-renseigné.

    :param name: nom du logger (typiquement ``__name__``).
    :param initial_values: valeurs contextuelles ajoutées à chaque log
        (par exemple ``component="rag"`` ou ``task="ingest_document"``).
    """
    logger = structlog.get_logger(name)
    if initial_values:
        return logger.bind(**initial_values)
    return logger


def bind_contextvars(**values: Any) -> MutableMapping[str, Token[Any]]:
    """Lie des valeurs contextuelles partagées à tous les logs suivants.

    Utile pour attacher ``user_id``, ``model`` ou ``retrieved_documents`` au
    contexte du Pipeline_RAG (Exigence 30.2). Les valeurs restent actives
    jusqu'à :func:`structlog.contextvars.clear_contextvars`.
    """
    return structlog.contextvars.bind_contextvars(**values)
