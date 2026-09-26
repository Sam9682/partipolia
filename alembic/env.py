"""Environnement de migration Alembic — async (Exigences 32.4, 32.3, 33.1).

Ce module configure Alembic pour un moteur SQLAlchemy 2.x **async** (driver
``psycopg`` 3). Points clés :

* l'URL de connexion est lue depuis ``settings.database_url`` (variable
  ``DATABASE_URL``) — **aucun secret n'est stocké dans Git** (Exigence 32.6) ;
* ``target_metadata`` pointe sur ``Base.metadata`` pour l'``autogenerate`` ;
* tous les modèles de ``app.models`` sont importés afin d'être enregistrés dans
  les métadonnées avant la détection des changements ;
* l'extension PostgreSQL ``vector`` (pgvector) est activée en début de migration
  afin que les colonnes ``VECTOR`` et les index HNSW soient utilisables
  (Exigences 11.6, 32.3).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Configuration applicative & métadonnées cibles
# ---------------------------------------------------------------------------
from app.core.config import settings
from app.core.database import Base

# Importe le paquet des modèles pour que toutes les tables soient enregistrées
# dans ``Base.metadata`` avant l'autogenerate. L'import est tolérant : au tout
# début du projet, ``app.models`` peut ne contenir aucun modèle.
try:  # pragma: no cover - simple garde-fou d'import
    import app.models  # noqa: F401
except ImportError:  # pragma: no cover
    pass

# Objet de configuration Alembic, donnant accès aux valeurs du fichier .ini.
config = context.config

# Renseigne dynamiquement l'URL de connexion (jamais écrite dans alembic.ini).
config.set_main_option("sqlalchemy.url", settings.database_url)

# Configure la journalisation à partir du fichier .ini si présent.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Métadonnées cibles pour l'``--autogenerate``.
target_metadata = Base.metadata


def _ensure_pgvector_extension(connection: Connection) -> None:
    """Active l'extension pgvector ``vector`` si elle n'existe pas (Exigence 32.3)."""
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))


def run_migrations_offline() -> None:
    """Exécute les migrations en mode « hors-ligne » (génération de SQL).

    Aucune connexion n'est établie : Alembic émet le SQL. La création de
    l'extension ``vector`` est ajoutée en tête pour que le script produit soit
    directement applicable.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.execute("CREATE EXTENSION IF NOT EXISTS vector")
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Configure le contexte et exécute les migrations sur une connexion établie."""
    _ensure_pgvector_extension(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Crée un moteur async, ouvre une connexion et lance les migrations."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
        # La connexion async ``AsyncConnection`` détient la transaction DBAPI
        # réelle. ``context.begin_transaction()`` (dans ``do_run_migrations``)
        # opère sur la façade *sync* et ne valide pas la transaction externe :
        # sans ce ``commit`` explicite, tout le DDL est annulé à la fermeture de
        # la connexion (``alembic upgrade`` réussit mais ne crée aucune table).
        await connection.commit()

    await connectable.dispose()


def run_migrations_online() -> None:
    """Point d'entrée « en ligne » : exécute la coroutine de migration async."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
