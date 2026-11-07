"""Verrouillage des tentatives de connexion (``LoginThrottle`` — Exigences 15.8 à 15.10).

Principe :

* chaque échec de connexion incrémente ``loginfail:{sha256(identifiant normalisé)}``
  (TTL 900 s posé au premier échec : fenêtre de 15 minutes) ;
* au 10ᵉ échec dans la fenêtre, le verrou ``loginlock:{hash}`` (TTL 900 s) est posé
  par ``SET NX`` et **un seul** événement « échecs répétés » est écrit dans le
  Journal_Audit (Exigence 15.8) ;
* tant que le verrou existe, :meth:`LoginThrottle.is_locked` renvoie les secondes
  restantes et l'appelant répond ``429`` avec ``Retry-After`` (Exigence 15.9).

La clé dépend de l'**identifiant saisi**, jamais de l'existence du compte : un
identifiant inconnu est verrouillé exactement comme un identifiant connu
(Exigence 15.10). Aucun identifiant en clair n'est stocké dans Redis ni dans
l'audit (seul son empreinte SHA-256).

Échec ouvert : si Redis est indisponible, aucune restriction n'est appliquée et
l'incident est journalisé (comme ``RateLimiter``).
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from redis.asyncio import Redis

from app.core.logging import get_logger
from app.core.redis import get_redis

if TYPE_CHECKING:  # évite un import circulaire core ↔ services
    from app.services.audit_service import AuditService

__all__ = [
    "LoginThrottle",
    "ThrottleState",
    "MAX_FAILURES",
    "WINDOW_SECONDS",
    "LOCK_SECONDS",
    "ACTION_REPEATED_FAILURES",
]

logger = get_logger(__name__)

MAX_FAILURES: Final = 10
WINDOW_SECONDS: Final = 900
LOCK_SECONDS: Final = 900
ACTION_REPEATED_FAILURES: Final = "auth.login_repeated_failures"

_FAIL_PREFIX: Final = "loginfail"
_LOCK_PREFIX: Final = "loginlock"


@dataclass(frozen=True, slots=True)
class ThrottleState:
    """État après l'enregistrement d'un échec."""

    failures: int
    locked: bool
    retry_after: int | None = None


def normalize_identifier(identifier: str) -> str:
    """Normalise l'identifiant saisi (NFKC, minuscules, espaces retirés)."""
    return unicodedata.normalize("NFKC", identifier).strip().casefold()


def identifier_hash(identifier: str) -> str:
    """Empreinte SHA-256 de l'identifiant normalisé."""
    return hashlib.sha256(normalize_identifier(identifier).encode("utf-8")).hexdigest()


class LoginThrottle:
    """Compteur d'échecs de connexion et verrou temporaire, adossés à Redis."""

    def __init__(
        self,
        *,
        redis: Redis | None = None,
        audit: AuditService | None = None,
        max_failures: int = MAX_FAILURES,
        window_seconds: int = WINDOW_SECONDS,
        lock_seconds: int = LOCK_SECONDS,
    ) -> None:
        self._redis = redis
        self._audit = audit
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self.lock_seconds = lock_seconds

    @property
    def redis(self) -> Redis:
        return self._redis if self._redis is not None else get_redis()

    async def register_failure(
        self, identifier: str, *, ip_hash: str | None = None
    ) -> ThrottleState:
        """Comptabilise un échec ; pose le verrou et audite au seuil."""
        digest = identifier_hash(identifier)
        fail_key = f"{_FAIL_PREFIX}:{digest}"
        lock_key = f"{_LOCK_PREFIX}:{digest}"
        try:
            count = int(await self.redis.incr(fail_key))
            if count == 1:
                await self.redis.expire(fail_key, self.window_seconds)
            if count < self.max_failures:
                return ThrottleState(failures=count, locked=False)
            # SET NX : un seul appelant obtient le verrou, donc un seul audit.
            acquired = await self.redis.set(lock_key, "1", ex=self.lock_seconds, nx=True)
        except Exception:  # noqa: BLE001 — Redis indisponible : échec ouvert.
            logger.warning("login_throttle_backend_unavailable", exc_info=True)
            return ThrottleState(failures=0, locked=False)

        if acquired:
            await self._audit_repeated_failures(digest, count, ip_hash)
        return ThrottleState(failures=count, locked=True, retry_after=self.lock_seconds)

    async def is_locked(self, identifier: str) -> int | None:
        """Secondes restantes de verrou, ou ``None`` si l'identifiant n'est pas bloqué."""
        lock_key = f"{_LOCK_PREFIX}:{identifier_hash(identifier)}"
        try:
            if not await self.redis.exists(lock_key):
                return None
            ttl = int(await self.redis.ttl(lock_key))
        except Exception:  # noqa: BLE001 — échec ouvert.
            logger.warning("login_throttle_backend_unavailable", exc_info=True)
            return None
        if ttl == -2:  # expiré entre-temps
            return None
        return ttl if ttl > 0 else self.lock_seconds

    async def _audit_repeated_failures(self, digest: str, count: int, ip_hash: str | None) -> None:
        if self._audit is None:
            return
        await self._audit.record_isolated(
            action=ACTION_REPEATED_FAILURES,
            entity_type="auth",
            new_data={
                "identifier_hash": digest,
                "failures": count,
                "window_seconds": self.window_seconds,
            },
            ip_hash=ip_hash,
        )
