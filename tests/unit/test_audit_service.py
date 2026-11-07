"""Tests unitaires de ``AuditService`` (actor_id, record_isolated)."""

from __future__ import annotations

from typing import Any

from app.models.audit import AuditLog
from app.services.audit_service import AuditService


class _FakeSession:
    def __init__(self, fail_commit: bool = False) -> None:
        self.added: list[Any] = []
        self.committed = False
        self._fail = fail_commit

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        if self._fail:
            raise RuntimeError("db down")
        self.committed = True


async def test_record_stores_actor_id() -> None:
    session = _FakeSession()
    entry = await AuditService(session).record(  # type: ignore[arg-type]
        action="a", entity_type="e", actor_id=7, new_data={"region_code": "75"}
    )
    assert entry.actor_id == 7
    assert session.added == [entry]


async def test_record_isolated_commits_in_dedicated_session() -> None:
    main, dedicated = _FakeSession(), _FakeSession()
    ok = await AuditService(main, session_factory=lambda: dedicated).record_isolated(  # type: ignore[arg-type,return-value]
        action="a", entity_type="e", actor_id=3, ip_hash="h"
    )
    assert ok is True
    assert dedicated.committed and not main.added
    assert isinstance(dedicated.added[0], AuditLog)
    assert dedicated.added[0].actor_id == 3


async def test_record_isolated_swallows_write_failure() -> None:
    failing = _FakeSession(fail_commit=True)
    ok = await AuditService(_FakeSession(), session_factory=lambda: failing).record_isolated(  # type: ignore[arg-type,return-value]
        action="a", entity_type="e"
    )
    assert ok is False
