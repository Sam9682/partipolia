"""Archived legacy API routers of the former political platform (PARTIPOLAI).

These routers are kept importable but are **not mounted** by :mod:`app.main`
("archive and hide"). To temporarily re-expose them, include
:data:`legacy_api_router` in ``app.api.v1.router``.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import (
    arguments,
    chat,
    documents,
    legal_problems,
    mandate,
    program,
    proposals,
    sources,
    statistics,
    teams,
    themes,
    votes,
)

legacy_api_router: APIRouter = APIRouter()

legacy_api_router.include_router(themes.router, prefix="/themes", tags=["legacy"])
legacy_api_router.include_router(proposals.router, prefix="/proposals", tags=["legacy"])
legacy_api_router.include_router(sources.router, prefix="/sources", tags=["legacy"])
legacy_api_router.include_router(
    sources.proposal_sources_router, prefix="/proposals", tags=["legacy"]
)
legacy_api_router.include_router(documents.router, prefix="/admin", tags=["legacy"])
legacy_api_router.include_router(arguments.router, prefix="/arguments", tags=["legacy"])
legacy_api_router.include_router(
    arguments.proposal_arguments_router, prefix="/proposals", tags=["legacy"]
)
legacy_api_router.include_router(votes.router, prefix="/proposals", tags=["legacy"])
legacy_api_router.include_router(statistics.router, prefix="/statistics", tags=["legacy"])
legacy_api_router.include_router(program.router, prefix="/program", tags=["legacy"])
legacy_api_router.include_router(teams.router, prefix="/teams", tags=["legacy"])
legacy_api_router.include_router(mandate.router, prefix="/mandate", tags=["legacy"])
legacy_api_router.include_router(chat.router, prefix="/chat", tags=["legacy"])
legacy_api_router.include_router(legal_problems.router, prefix="/legal-problems", tags=["legacy"])
