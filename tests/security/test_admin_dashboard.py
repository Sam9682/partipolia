"""Admin dashboard access control and role-dependent content."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.deps import get_current_user_optional
from app.core.database import get_session
from app.core.middleware import register_exception_handlers
from app.schemas.auth import UserPublic
from app.web import admin as admin_module
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api


class _Result:
    def scalars(self) -> _Result:
        return self

    def all(self) -> list:
        return []


class _StubSession:
    async def execute(self, *_args, **_kwargs) -> _Result:
        return _Result()


class _StubService:
    def __init__(self, _session) -> None: ...

    async def list_datasets(self) -> list:
        return []

    async def list_versions(self) -> list:
        return []


def _user(role: str) -> UserPublic:
    now = datetime.now(UTC)
    return UserPublic(
        id=1,
        email="u@example.org",
        display_name="U",
        is_active=True,
        is_verified=True,
        is_admin=False,
        role=role,
        created_at=now,
        updated_at=now,
    )


def _client(role: str | None, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setattr(admin_module, "EconomicDataService", _StubService)
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(admin_module.admin_web_router)
    app.dependency_overrides[get_session] = lambda: _StubSession()
    app.dependency_overrides[get_current_user_optional] = lambda: _user(role) if role else None
    return TestClient(app, raise_server_exceptions=False, follow_redirects=False)


def test_anonymous_is_redirected_to_login(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _client(None, monkeypatch).get("/admin")
    assert response.status_code == 303 and response.headers["location"] == "/login"


@pytest.mark.parametrize("role", ["citizen", "moderator", "content_admin"])
def test_other_roles_are_forbidden(role: str, monkeypatch: pytest.MonkeyPatch) -> None:
    assert _client(role, monkeypatch).get("/admin").status_code == 403


def test_data_admin_sees_data_but_not_users(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _client("data_admin", monkeypatch).get("/admin").text
    assert 'data-testid="admin-datasets"' in html and 'data-testid="admin-audit"' in html
    assert 'data-testid="admin-users"' not in html


def test_super_admin_also_sees_users(monkeypatch: pytest.MonkeyPatch) -> None:
    html = _client("super_admin", monkeypatch).get("/admin").text
    assert 'data-testid="admin-users"' in html
