"""Role-based access control of the administration endpoints."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from app.api.deps import get_current_user
from app.api.v1 import economic_data
from app.core.middleware import register_exception_handlers
from app.models.roles import Role, has_role
from app.schemas.auth import UserPublic
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.api

IMPORT_BODY = {
    "name": "n",
    "source": "s",
    "reference_year": 2026,
    "csv_text": "category_code,parameter_type,key,low,central,high,unit,confidence\n",
}


class _FakeService:
    """Records calls; raises nothing. Authorization must happen before the service."""

    calls = 0

    async def import_csv(self, *args, **kwargs):
        type(self).calls += 1
        raise economic_data.DatasetValidationError(["stub"])


def _user(role: str) -> UserPublic:
    now = datetime.now(UTC)
    return UserPublic(
        id=1,
        email="u@example.org",
        display_name="U",
        is_active=True,
        is_verified=True,
        is_admin=role == "super_admin",
        role=role,
        created_at=now,
        updated_at=now,
    )


def _client(role: str | None) -> TestClient:
    app = FastAPI()
    register_exception_handlers(app)
    app.include_router(economic_data.admin_router, prefix="/api/v1/admin")
    app.dependency_overrides[economic_data.get_service] = lambda: _FakeService()
    if role is not None:
        app.dependency_overrides[get_current_user] = lambda: _user(role)
    return TestClient(app, raise_server_exceptions=False)


def test_unauthenticated_gets_401() -> None:
    response = _client(None).post("/api/v1/admin/economic-data/import", json=IMPORT_BODY)
    assert response.status_code == 401


@pytest.mark.parametrize("role", ["citizen", "moderator", "content_admin"])
def test_insufficient_role_gets_403_and_service_is_not_called(role: str) -> None:
    _FakeService.calls = 0
    response = _client(role).post("/api/v1/admin/economic-data/import", json=IMPORT_BODY)
    assert response.status_code == 403
    assert _FakeService.calls == 0


@pytest.mark.parametrize("role", ["data_admin", "super_admin"])
def test_authorized_roles_reach_the_service(role: str) -> None:
    _FakeService.calls = 0
    response = _client(role).post("/api/v1/admin/economic-data/import", json=IMPORT_BODY)
    assert response.status_code == 400  # the stub's validation error, not an auth error
    assert _FakeService.calls == 1


@pytest.mark.parametrize(
    "path", ["publish", "model-versions", "compare?from_id=1&to_id=2", "import"]
)
def test_every_admin_route_is_guarded(path: str) -> None:
    client = _client("citizen")
    url = f"/api/v1/admin/economic-data/{path}"
    response = client.get(url) if path.startswith("compare") else client.post(url, json={})
    assert response.status_code == 403


def test_has_role_semantics() -> None:
    assert has_role("super_admin", Role.DATA_ADMIN)
    assert has_role("data_admin", Role.DATA_ADMIN)
    assert not has_role("citizen", Role.DATA_ADMIN)
    assert not has_role("moderator", Role.DATA_ADMIN, Role.CONTENT_ADMIN)
    assert has_role("moderator", Role.MODERATOR)
