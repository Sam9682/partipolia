# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# PARTIPOLAI — Image du service Web (API + SSR)  (Exigences 32.1, 33.1)
# Base minimale Python 3.13, gestion des dépendances via `uv`, exécution en
# utilisateur non-root (Exigence 26 — durcissement).
# ---------------------------------------------------------------------------
FROM python:3.13-slim AS base

# Réglages Python et uv sains pour l'exécution en conteneur.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

# Dépendances système minimales : curl pour les health checks, libpq pour psycopg.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Installer `uv` (copie du binaire statique depuis l'image officielle).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# ---------------------------------------------------------------------------
# Couche dépendances : mise en cache indépendante du code applicatif.
# On installe d'abord les dépendances déclarées (sans le projet lui-même) afin
# de tirer parti du cache Docker tant que pyproject/uv.lock ne changent pas.
# ---------------------------------------------------------------------------
COPY pyproject.toml README.md ./
COPY uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra openai --no-install-project --no-dev

# Copier le code applicatif puis finaliser l'installation du paquet `app`.
COPY app ./app
COPY alembic.ini* ./
COPY alembic ./alembic
COPY scripts ./scripts
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --extra openai --no-dev

# Rendre le point d'entrée exécutable (applique les migrations au démarrage).
RUN chmod +x /app/scripts/entrypoint.sh

# ---------------------------------------------------------------------------
# Utilisateur non-root (Exigence 26 — surface d'attaque réduite).
# ---------------------------------------------------------------------------
RUN groupadd --system app && useradd --system --gid app --home-dir /app app \
    && chown -R app:app /app /opt/venv
USER app

EXPOSE 8000

# Health check applicatif (Exigence 27.1) : /health répond {status: ok}.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# Applique les migrations Alembic (idempotent) puis lance le serveur.
ENTRYPOINT ["/app/scripts/entrypoint.sh"]

# Serveur ASGI. En production, Gunicorn pilote des workers Uvicorn ; le nombre
# de workers reste surchargeable via la variable d'environnement WEB_CONCURRENCY.
CMD ["gunicorn", "app.main:app", \
     "--worker-class", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
