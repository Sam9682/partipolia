"""Fixtures de test partagées pour PARTIPOLAI.

Ce module fournit un moteur SQLAlchemy **async** et une ``AsyncSession`` adossés à
une vraie base de données pour les tests qui exercent la couche de persistance
(Services + ORM), par opposition aux tests unitaires de logique pure qui utilisent
des sessions factices.

La base cible est **PostgreSQL** : c'est le SGBD de production (Exigences 32.2,
32.3) et le seul qui implémente l'``UPSERT`` ``ON CONFLICT`` utilisé par
``VoteService`` (Exigences 6.2, 6.3). En intégration continue, un service
``pgvector/pgvector:pg16`` est fourni et ``DATABASE_URL`` pointe dessus
(cf. ``.github/workflows/ci.yml``). En local, si aucune base PostgreSQL n'est
joignable (ou si le driver async ``psycopg`` 3 est absent), les tests qui en
dépendent sont **ignorés** proprement plutôt que d'échouer.

Seules les tables strictement nécessaires (``users``, ``themes``, ``proposals``,
``votes``) sont créées, afin d'éviter d'exiger l'extension ``vector`` requise par
``document_chunks``.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio


def _candidate_database_url() -> str | None:
    """Retourne l'URL PostgreSQL async à utiliser pour les tests, si définie.

    Priorité à ``TEST_DATABASE_URL`` puis ``DATABASE_URL``. On n'accepte qu'une
    URL PostgreSQL : l'``UPSERT`` de ``VoteService`` est spécifique à ce dialecte.
    """
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    if not url.startswith("postgresql"):
        return None
    # Force le driver async psycopg 3 attendu par l'application.
    if "+psycopg" not in url:
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[object]:
    """Fournit une ``AsyncSession`` sur une base PostgreSQL de test isolée.

    Le schéma minimal (users, themes, proposals, votes) est (re)créé avant le
    test et supprimé après, pour garantir l'isolation. Si aucune base PostgreSQL
    n'est joignable, le test appelant est ignoré.
    """
    url = _candidate_database_url()
    if url is None:
        pytest.skip(
            "Aucune base PostgreSQL de test disponible "
            "(définir TEST_DATABASE_URL ou DATABASE_URL vers PostgreSQL)."
        )

    try:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    except Exception as exc:  # pragma: no cover - environnement sans SQLAlchemy async
        pytest.skip(f"SQLAlchemy async indisponible : {exc}")

    # Importe les modèles requis pour peupler ``Base.metadata`` avec uniquement
    # les tables nécessaires à la propriété testée.
    from app.core.database import Base
    from app.models.proposal import Proposal  # noqa: F401
    from app.models.theme import Theme  # noqa: F401
    from app.models.user import User  # noqa: F401
    from app.models.vote import Vote  # noqa: F401

    required_tables = [
        Base.metadata.tables[name]
        for name in ("users", "themes", "proposals", "votes")
    ]

    try:
        engine = create_async_engine(url, future=True)
    except Exception as exc:  # pragma: no cover - driver manquant
        pytest.skip(f"Impossible de créer le moteur async ({url!r}) : {exc}")

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=required_tables)
            await conn.run_sync(Base.metadata.create_all, tables=required_tables)
    except Exception as exc:  # pragma: no cover - base non joignable en local
        await engine.dispose()
        pytest.skip(f"Base PostgreSQL de test non joignable : {exc}")

    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    session = session_factory()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=required_tables)
        await engine.dispose()


ECONOMIC_TABLES = (
    "users",
    "audit_logs",
    "consumption_categories",
    "economic_datasets",
    "fiscal_parameters",
    "model_versions",
    "model_version_datasets",
    "economic_simulations",
    "simulation_results",
    "citizen_commitments",
    "aggregated_impacts",
)


@pytest_asyncio.fixture
async def econ_session() -> AsyncIterator[object]:
    """``AsyncSession`` on a PostgreSQL database with the Partipolia economic schema.

    The six default categories are seeded. Skipped when no PostgreSQL is reachable
    (set ``TEST_DATABASE_URL`` to enable).
    """
    url = _candidate_database_url()
    if url is None:
        pytest.skip("Aucune base PostgreSQL de test disponible (TEST_DATABASE_URL).")

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401  (registers every table on Base.metadata)
    from app.core.database import Base
    from app.economics.categories import DEFAULT_CATEGORIES
    from app.models.consumption_category import ConsumptionCategory

    tables = [Base.metadata.tables[name] for name in ECONOMIC_TABLES]
    engine = create_async_engine(url, future=True)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=tables)
            await conn.run_sync(Base.metadata.create_all, tables=tables)
    except Exception as exc:  # pragma: no cover
        await engine.dispose()
        pytest.skip(f"Base PostgreSQL de test non joignable : {exc}")

    factory = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    session = factory()
    session.add_all(
        ConsumptionCategory(
            code=c.code,
            name=c.name,
            description=c.description,
            reference_annual_expenditure=c.reference_annual_expenditure,
        )
        for c in DEFAULT_CATEGORIES
    )
    await session.commit()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all, tables=tables)
        await engine.dispose()
