"""Limitation de débit basée sur Redis (``RateLimiter`` — Exigences 26.4, 26.5).

La Plateforme applique une limitation de débit **basique** sur ses points d'accès
sensibles (connexion, inscription, chat, propositions, commentaires, votes) afin
de contenir les abus (Exigence 26.4), avec une **protection renforcée** du point
d'accès ``POST /api/v1/chat`` (Exigence 26.5).

Modèle retenu — fenêtre fixe (« fixed window ») :

* une clé Redis ``ratelimit:{scope}:{identité}:{fenêtre}`` compte les requêtes
  d'un client sur un point d'accès pendant une fenêtre de durée
  ``RATE_LIMIT_WINDOW_SECONDS`` ;
* le compteur est incrémenté atomiquement (``INCR``) puis, au premier incrément,
  reçoit une expiration (``EXPIRE``) égale à la fenêtre : Redis purge donc
  automatiquement les compteurs inactifs, sans tâche de nettoyage ;
* lorsque le compteur dépasse le seuil du point d'accès, une réponse ``429``
  ``RATE_LIMITED`` est renvoyée avec l'en-tête ``Retry-After`` (secondes restantes
  avant réouverture de la fenêtre — Exigence 26.5, tableau des codes d'erreur).

L'identité du client est dérivée, par ordre de préférence, de l'``Utilisateur``
authentifié (cookie/JWT déjà résolu par la requête) sinon de l'adresse IP source
(``X-Forwarded-For`` en présence d'un proxy de bordure comme Nginx, sinon
``request.client``). Le modèle d'erreur ``429`` normalisé (en-tête ``Retry-After``
préservé) est produit par le gestionnaire global d'exceptions de
:mod:`app.core.middleware` à partir de l'``HTTPException`` levée ici.

La limitation est conçue pour **échouer en mode ouvert** (« fail-open ») : si Redis
est momentanément indisponible, la requête n'est pas bloquée (la disponibilité du
service prime sur une limitation basique), l'incident étant journalisé.
"""

from __future__ import annotations

import time
from typing import Final

from fastapi import Request, status
from fastapi.exceptions import HTTPException
from redis.asyncio import Redis

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.core.redis import get_redis

__all__ = [
    "RateLimiter",
    "rate_limiter_dependency",
    "default_rate_limit",
    "chat_rate_limit",
]

logger = get_logger(__name__)

# Préfixe des clés de compteur dans Redis.
_KEY_PREFIX: Final = "ratelimit"

# Message générique renvoyé au client sur 429 (aucune fuite d'information).
_RATE_LIMITED_MESSAGE: Final = "Trop de requêtes. Veuillez réessayer plus tard."


class RateLimiter:
    """Compteur de requêtes à fenêtre fixe adossé à Redis (Exigences 26.4, 26.5).

    Chaque instance est liée à un ``scope`` (le point d'accès protégé, par ex.
    ``"login"`` ou ``"chat"``), un ``max_requests`` (seuil autorisé par fenêtre) et
    une ``window_seconds`` (durée de la fenêtre). Une même identité cliente est
    comptée indépendamment par ``scope`` : le budget du chat est distinct de celui
    des propositions.
    """

    def __init__(
        self,
        *,
        scope: str,
        max_requests: int,
        window_seconds: int,
        redis: Redis | None = None,
    ) -> None:
        self.scope = scope
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._redis = redis

    @property
    def redis(self) -> Redis:
        """Client Redis utilisé (partagé par défaut, injectable pour les tests)."""
        return self._redis if self._redis is not None else get_redis()

    def _window_start(self, now: float) -> int:
        """Retourne l'instant de début de la fenêtre courante (arrondi à la fenêtre)."""
        return int(now // self.window_seconds) * self.window_seconds

    def _key(self, identity: str, window_start: int) -> str:
        """Construit la clé Redis d'un client pour la fenêtre courante."""
        return f"{_KEY_PREFIX}:{self.scope}:{identity}:{window_start}"

    async def check(self, identity: str, *, now: float | None = None) -> None:
        """Comptabilise une requête et lève ``429`` si le seuil est dépassé.

        Incrémente atomiquement le compteur de l'identité pour la fenêtre courante,
        pose l'expiration au premier incrément, et — si le compteur dépasse
        ``max_requests`` — lève une :class:`HTTPException` ``429`` portant l'en-tête
        ``Retry-After`` (secondes restant jusqu'à la fin de la fenêtre). En cas
        d'indisponibilité de Redis, la requête est **autorisée** (fail-open) et
        l'incident journalisé.
        """
        current = now if now is not None else time.time()
        window_start = self._window_start(current)
        key = self._key(identity, window_start)

        try:
            count = await self.redis.incr(key)
            # Au tout premier incrément de la fenêtre, on borne la durée de vie de
            # la clé : Redis purge alors automatiquement le compteur (Exigence 26.4).
            if count == 1:
                await self.redis.expire(key, self.window_seconds)
        except Exception:  # noqa: BLE001 — Redis indisponible : on ne bloque pas.
            logger.warning(
                "rate_limit_backend_unavailable",
                scope=self.scope,
                exc_info=True,
            )
            return

        if count > self.max_requests:
            retry_after = self._retry_after(current, window_start)
            logger.info(
                "rate_limited",
                scope=self.scope,
                identity=identity,
                count=count,
                limit=self.max_requests,
                retry_after=retry_after,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=_RATE_LIMITED_MESSAGE,
                headers={"Retry-After": str(retry_after)},
            )

    def _retry_after(self, now: float, window_start: int) -> int:
        """Secondes restantes avant la réouverture de la fenêtre (≥ 1)."""
        elapsed = now - window_start
        remaining = self.window_seconds - elapsed
        # Toujours ≥ 1 : un ``Retry-After: 0`` inviterait à réessayer immédiatement.
        return max(1, int(remaining) + (1 if remaining % 1 else 0))


def _client_identity(request: Request) -> str:
    """Dérive l'identité du client soumis à la limitation de débit.

    Préfère l'``Utilisateur`` authentifié (``request.state.user`` si une garde l'a
    déposé), sinon l'adresse IP source. En présence d'un proxy de bordure, le
    premier segment de ``X-Forwarded-For`` est utilisé ; à défaut,
    ``request.client.host``. Retourne ``"anonymous"`` si aucune source n'est
    exploitable.
    """
    user = getattr(request.state, "user", None)
    user_id = getattr(user, "id", None)
    if user_id is not None:
        return f"user:{user_id}"

    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return f"ip:{first}"

    client = request.client
    if client is not None and client.host:
        return f"ip:{client.host}"

    return "ip:anonymous"


def rate_limiter_dependency(
    scope: str,
    *,
    reinforced: bool = False,
):
    """Fabrique une dépendance FastAPI appliquant la limitation de débit à un point.

    :param scope: identifiant logique du point d'accès (``"login"``, ``"chat"``…),
        servant de préfixe aux compteurs Redis pour isoler les budgets.
    :param reinforced: si vrai, applique le seuil renforcé du chat
        (``RATE_LIMIT_CHAT_MAX``) plutôt que le seuil par défaut
        (``RATE_LIMIT_DEFAULT_MAX``) — protection accrue de ``POST /api/v1/chat``
        (Exigence 26.5).

    La dépendance ne bloque rien lorsque la limitation est désactivée
    (``RATE_LIMIT_ENABLED=false``), ce qui simplifie le développement et les tests.
    """

    async def _dependency(request: Request) -> None:
        settings: Settings = get_settings()
        if not settings.rate_limit_enabled:
            return
        max_requests = (
            settings.rate_limit_chat_max
            if reinforced
            else settings.rate_limit_default_max
        )
        limiter = RateLimiter(
            scope=scope,
            max_requests=max_requests,
            window_seconds=settings.rate_limit_window_seconds,
        )
        await limiter.check(_client_identity(request))

    return _dependency


# Dépendances prêtes à l'emploi pour les routeurs.
#
# ``default_rate_limit`` couvre les points d'accès sensibles standards ; on la
# paramètre par ``scope`` afin que chaque famille de points ait son propre budget.
def default_rate_limit(scope: str):
    """Dépendance de limitation au seuil par défaut pour un ``scope`` donné."""
    return rate_limiter_dependency(scope, reinforced=False)


# Dépendance renforcée dédiée au chat (Exigence 26.5).
chat_rate_limit = rate_limiter_dependency("chat", reinforced=True)
