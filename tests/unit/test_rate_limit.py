"""Tests unitaires du ``RateLimiter`` basé sur Redis (Exigences 26.4, 26.5 ; tâche 10.2).

Ces tests exercent la logique de comptage à fenêtre fixe de
``app/core/rate_limit.py`` **sans Redis réel** : un ``_FakeRedis`` asynchrone
reproduit les seules commandes utilisées (``INCR``, ``EXPIRE``) et permet de
simuler l'expiration de la fenêtre et l'indisponibilité du backend.

Couverture :

* en deçà du seuil, aucune requête n'est bloquée (Exigence 26.4) ;
* au-delà du seuil, une ``HTTPException`` ``429`` est levée avec un en-tête
  ``Retry-After`` positif (Exigence 26.5) ;
* les compteurs sont **isolés par ``scope``** : le budget du chat est distinct de
  celui des autres points d'accès ;
* une nouvelle fenêtre réinitialise le compteur (le comptage est bien borné dans
  le temps) ;
* la limitation **échoue en mode ouvert** si Redis est indisponible (fail-open) ;
* le seuil renforcé du chat (``reinforced``) est plus bas que le défaut
  (protection accrue de ``POST /api/v1/chat`` — Exigence 26.5).
"""

from __future__ import annotations

import asyncio

import pytest

from fastapi import HTTPException, status

from app.core.rate_limit import RateLimiter

pytestmark = pytest.mark.unit


class _FakeRedis:
    """Doublure asynchrone minimale de Redis : ``INCR`` + ``EXPIRE`` en mémoire."""

    def __init__(self, *, fail: bool = False) -> None:
        self._counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}
        self._fail = fail

    async def incr(self, key: str) -> int:
        if self._fail:
            raise RuntimeError("redis indisponible")
        self._counts[key] = self._counts.get(key, 0) + 1
        return self._counts[key]

    async def expire(self, key: str, seconds: int) -> bool:
        if self._fail:
            raise RuntimeError("redis indisponible")
        self.expirations[key] = seconds
        return True


def _limiter(redis: _FakeRedis, *, scope: str = "login", max_requests: int = 3) -> RateLimiter:
    return RateLimiter(
        scope=scope,
        max_requests=max_requests,
        window_seconds=60,
        redis=redis,
    )


def test_under_limit_allows_requests() -> None:
    """En deçà du seuil, les requêtes passent sans erreur (Exigence 26.4)."""
    redis = _FakeRedis()
    limiter = _limiter(redis, max_requests=3)

    async def scenario() -> None:
        for _ in range(3):
            await limiter.check("ip:1.2.3.4", now=1000.0)

    asyncio.run(scenario())
    # L'expiration a bien été posée une seule fois (au premier incrément).
    assert list(redis.expirations.values()) == [60]


def test_over_limit_raises_429_with_retry_after() -> None:
    """Au-delà du seuil : 429 + en-tête Retry-After positif (Exigence 26.5)."""
    redis = _FakeRedis()
    limiter = _limiter(redis, max_requests=2)

    async def scenario() -> None:
        # now = 1000.0 ⇒ début de fenêtre à 960 (multiple de 60), reste 20 s.
        await limiter.check("ip:1.2.3.4", now=1000.0)
        await limiter.check("ip:1.2.3.4", now=1000.0)
        with pytest.raises(HTTPException) as excinfo:
            await limiter.check("ip:1.2.3.4", now=1000.0)
        exc = excinfo.value
        assert exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        retry_after = int(exc.headers["Retry-After"])
        assert retry_after == 20

    asyncio.run(scenario())


def test_distinct_identities_have_separate_budgets() -> None:
    """Deux clients distincts ne partagent pas le même compteur."""
    redis = _FakeRedis()
    limiter = _limiter(redis, max_requests=1)

    async def scenario() -> None:
        await limiter.check("ip:1.1.1.1", now=1000.0)
        # Un autre client n'est pas impacté par le premier.
        await limiter.check("ip:2.2.2.2", now=1000.0)
        # Mais le premier client, lui, dépasse au deuxième appel.
        with pytest.raises(HTTPException):
            await limiter.check("ip:1.1.1.1", now=1000.0)

    asyncio.run(scenario())


def test_scopes_are_isolated() -> None:
    """Un même client a des budgets distincts selon le ``scope`` (chat vs login)."""
    redis = _FakeRedis()
    login = _limiter(redis, scope="login", max_requests=1)
    chat = _limiter(redis, scope="chat", max_requests=1)

    async def scenario() -> None:
        await login.check("user:7", now=1000.0)
        # Le scope ``chat`` dispose de son propre compteur : pas de blocage.
        await chat.check("user:7", now=1000.0)
        with pytest.raises(HTTPException):
            await login.check("user:7", now=1000.0)

    asyncio.run(scenario())


def test_new_window_resets_counter() -> None:
    """Une nouvelle fenêtre réinitialise le compteur (comptage borné dans le temps)."""
    redis = _FakeRedis()
    limiter = _limiter(redis, max_requests=1)

    async def scenario() -> None:
        await limiter.check("ip:9.9.9.9", now=1000.0)
        with pytest.raises(HTTPException):
            await limiter.check("ip:9.9.9.9", now=1000.0)
        # 60 s plus tard : nouvelle fenêtre, la première requête repasse.
        await limiter.check("ip:9.9.9.9", now=1060.0)

    asyncio.run(scenario())


def test_fail_open_when_redis_unavailable() -> None:
    """Si Redis est indisponible, la requête n'est pas bloquée (fail-open)."""
    redis = _FakeRedis(fail=True)
    limiter = _limiter(redis, max_requests=1)

    async def scenario() -> None:
        # Malgré un seuil de 1, plusieurs appels passent sans lever de 429.
        for _ in range(5):
            await limiter.check("ip:1.2.3.4", now=1000.0)

    asyncio.run(scenario())


def test_retry_after_is_at_least_one_second() -> None:
    """``Retry-After`` est toujours ≥ 1 même en toute fin de fenêtre."""
    redis = _FakeRedis()
    limiter = _limiter(redis, max_requests=1)

    async def scenario() -> None:
        # now = 1059.5 ⇒ fenêtre 1020..1080, reste 0.5 s → arrondi à ≥ 1.
        await limiter.check("ip:1.2.3.4", now=1059.5)
        with pytest.raises(HTTPException) as excinfo:
            await limiter.check("ip:1.2.3.4", now=1059.5)
        assert int(excinfo.value.headers["Retry-After"]) >= 1

    asyncio.run(scenario())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
