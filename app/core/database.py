"""Couche d'accès à la base de données (Exigences 32.2, 32.3, 33.1).

Fournit le moteur SQLAlchemy 2.x **async** (driver ``psycopg`` 3), la fabrique
de sessions ``async_sessionmaker``, la classe déclarative de base ``Base`` pour
les modèles ORM (style ``Mapped`` typé) et la dépendance FastAPI ``get_session``
qui fournit une ``AsyncSession`` transactionnelle par requête.

Le monolithe modulaire (Exigence 33.1) partage ce moteur unique entre l'API, la
couche de Services, le RAG et le Worker Celery.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    """Classe déclarative de base pour tous les modèles ORM.

    Les modèles de ``app/models`` héritent de cette classe et utilisent le style
    ``Mapped`` typé de SQLAlchemy 2.x, avec ``relationship(lazy="selectin")``
    pour éviter le problème N+1 (voir la conception, section couche de données).
    """


# ---------------------------------------------------------------------------
# Moteur & fabrique de sessions
# ---------------------------------------------------------------------------
# ``DATABASE_URL`` doit utiliser le driver async psycopg 3, p. ex.
# ``postgresql+psycopg://user:pass@host:5432/db`` (voir .env.example).
engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
)

# ``expire_on_commit=False`` permet de continuer à lire les attributs des objets
# après un commit sans déclencher de rechargement (utile pour renvoyer des DTO).
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


# ---------------------------------------------------------------------------
# Dépendance de session FastAPI
# ---------------------------------------------------------------------------
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Dépendance FastAPI fournissant une ``AsyncSession`` par requête.

    La session est commitée si le bloc s'exécute sans erreur, annulée en cas
    d'exception, puis systématiquement fermée. Les routeurs et Services la
    reçoivent via ``Depends(get_session)``.
    """
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Libère le pool de connexions du moteur (arrêt de l'application)."""
    await engine.dispose()
