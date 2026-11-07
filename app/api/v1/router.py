"""Aggregator router of the versioned REST API ``/api/v1``.

Partipolia exposes authentication, privacy (GDPR) and the economic-mobilization
endpoints. The former political-platform routers live in
:mod:`app.api.v1.legacy_router` and are archived (not mounted).
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import auth, commitments, dashboard, economic_data, privacy, simulations

api_router: APIRouter = APIRouter()

# Citizen commitments (declared), minimal profile and consent.
api_router.include_router(commitments.router, prefix="/commitments", tags=["commitments"])
api_router.include_router(commitments.profile_router, prefix="/citizens", tags=["citizens"])

# National dashboard — public aggregates with a minimum cohort threshold.
api_router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
api_router.include_router(dashboard.admin_router, prefix="/admin", tags=["admin"])

# Economic data — public sources/versions and administration (data_admin).
api_router.include_router(economic_data.router, prefix="/economic-data", tags=["economic-data"])
api_router.include_router(economic_data.admin_router, prefix="/admin", tags=["admin"])

# Personal simulator — anonymous, stateless, rate limited.
api_router.include_router(simulations.router, prefix="/simulations", tags=["simulations"])

# Authentication — register, login, refresh, logout, me.
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# GDPR — data export, consent management, account deletion, retention policy.
api_router.include_router(privacy.router, prefix="/privacy", tags=["privacy"])
