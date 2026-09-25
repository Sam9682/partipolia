"""Client Redis partagé (Exigences 32.3, 33.1).

Redis est utilisé de façon transversale par le monolithe modulaire :

* cache applicatif,
* broker/back-end des tâches Celery (Exigence 23),
* limitation de débit (``RateLimiter`` — Exigences 26.4, 26.5).

Ce module expose un client **async** unique et partagé (pool de connexions
interne géré par ``redis.asyncio``), obtenu via ``get_redis()``, ainsi qu'une
dépendance FastAPI ``get_redis_dependency`` pour l'injection dans les routeurs.
"""

from __future__ import annotations

from redis.asyncio import Redis, from_url

from app.core.config import settings

# Client Redis async partagé. ``decode_responses=True`` renvoie des ``str`` plutôt
# que des ``bytes`` (pratique pour le cache et les compteurs de rate limit).
redis_client: Redis = from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


def get_redis() -> Redis:
    """Retourne le client Redis async partagé."""
    return redis_client


async def get_redis_dependency() -> Redis:
    """Dépendance FastAPI fournissant le client Redis partagé.

    À utiliser via ``Depends(get_redis_dependency)`` dans les routeurs et
    Services ayant besoin du cache ou du rate limit.
    """
    return redis_client


async def close_redis() -> None:
    """Ferme le client Redis et son pool de connexions (arrêt de l'application)."""
    await redis_client.aclose()
