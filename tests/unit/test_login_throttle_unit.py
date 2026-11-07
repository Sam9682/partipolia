"""Tests unitaires de ``LoginThrottle`` (Exigences 15.8, 15.9, 15.10)."""

from __future__ import annotations

import pytest
from app.core.login_throttle import LoginThrottle

pytestmark = pytest.mark.unit


class _FakeRedis:
    def __init__(self, broken: bool = False) -> None:
        self.data: dict[str, int | str] = {}
        self.ttls: dict[str, int] = {}
        self.broken = broken

    def _check(self) -> None:
        if self.broken:
            raise ConnectionError("down")

    async def incr(self, key):
        self._check()
        self.data[key] = int(self.data.get(key, 0)) + 1
        return self.data[key]

    async def expire(self, key, seconds):
        self._check()
        self.ttls[key] = seconds

    async def set(self, key, value, ex=None, nx=False):
        self._check()
        if nx and key in self.data:
            return None
        self.data[key] = value
        self.ttls[key] = ex
        return True

    async def exists(self, key):
        self._check()
        return 1 if key in self.data else 0

    async def ttl(self, key):
        self._check()
        return self.ttls.get(key, -2)


class _FakeAudit:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def record_isolated(self, **kwargs):
        self.calls.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_lock_at_tenth_failure_with_single_audit_event() -> None:
    redis, audit = _FakeRedis(), _FakeAudit()
    throttle = LoginThrottle(redis=redis, audit=audit)  # type: ignore[arg-type]

    for _ in range(9):
        state = await throttle.register_failure(" Ghost@Example.com ")
        assert not state.locked
    assert await throttle.is_locked("ghost@example.com") is None

    state = await throttle.register_failure("ghost@example.com")
    assert state.locked
    assert await throttle.is_locked("GHOST@example.com") == 900
    assert len(audit.calls) == 1
    assert audit.calls[0]["action"] == "auth.login_repeated_failures"

    await throttle.register_failure("ghost@example.com")
    assert len(audit.calls) == 1


@pytest.mark.asyncio
async def test_fail_open_when_redis_unavailable() -> None:
    throttle = LoginThrottle(redis=_FakeRedis(broken=True))  # type: ignore[arg-type]
    assert not (await throttle.register_failure("a@b.c")).locked
    assert await throttle.is_locked("a@b.c") is None
