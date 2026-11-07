"""Administration dashboard (SSR, French).

Visibility by role:

* ``data_admin`` / ``super_admin``: datasets, model versions, audit log;
* ``super_admin``: users and their roles.

Anonymous visitors are redirected to the login page; authenticated users without
a sufficient role receive a generic 403.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import _audit_admin_denied, get_current_user_optional
from app.core.database import get_session
from app.models.audit import AuditLog
from app.models.roles import Role, has_role
from app.models.user import User
from app.schemas.auth import UserPublic
from app.services.economic_data_service import EconomicDataService
from app.web.public import page_context, resolve_header_identity, templates

admin_web_router = APIRouter(dependencies=[Depends(resolve_header_identity)])


@admin_web_router.get(
    "/admin", response_class=HTMLResponse, response_model=None, include_in_schema=False
)
async def admin_dashboard(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session, scope="function")],
    current_user: Annotated[UserPublic | None, Depends(get_current_user_optional)],
) -> HTMLResponse | RedirectResponse:
    if current_user is None:
        return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    can_data = has_role(current_user.role, Role.DATA_ADMIN)
    is_super = has_role(current_user.role, Role.SUPER_ADMIN)
    if not (can_data or is_super):
        await _audit_admin_denied(request, current_user)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Action non autorisée.")

    service = EconomicDataService(session)
    datasets = await service.list_datasets() if can_data else []
    versions = await service.list_versions() if can_data else []
    audit = (
        (await session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(50)))
        .scalars()
        .all()
        if can_data
        else []
    )
    users = (
        (await session.execute(select(User).order_by(User.id).limit(200))).scalars().all()
        if is_super
        else []
    )
    return templates.TemplateResponse(
        request,
        "public/admin.html",
        page_context(
            request,
            role=current_user.role,
            datasets=datasets,
            versions=versions,
            audit=audit,
            users=users,
            is_super=is_super,
        ),
    )
