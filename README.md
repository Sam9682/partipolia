# PARTIPOLAI

Plateforme de parti politique virtuel dont l'entité centrale est la **Proposition** de politique
publique. Architecture V1 : **monolithe modulaire** (Exigence 33).

## Stack

- **Python 3.13+**, **FastAPI**, **Pydantic v2**
- **SQLAlchemy 2.x** (async, style `Mapped` typé) + **psycopg 3** + **PostgreSQL 16 / pgvector**
- **Redis** (cache, broker Celery, rate limit) + **Celery** (ingestion, embeddings, modération)
- Rendu SSR **Jinja2 / HTMX / Alpine.js / Tailwind**
- RAG intégré au backend et au worker (recherche hybride lexicale + sémantique)

## Structure du projet

```
app/
  core/        configuration, base de données, Redis, logging, middleware
  models/      modèles SQLAlchemy 2.x
  schemas/     schémas Pydantic v2
  api/v1/      points d'accès REST versionnés
  services/    services métier
  rag/         pipeline RAG (classification, recherche, fusion, reranking, génération)
  rag/providers/  providers IA (LLM / Embedding) sélectionnés par configuration
  workers/     tâches Celery
  templates/   gabarits Jinja2
  static/      ressources statiques
tests/         unit · integration · api · rag
scripts/       seed · ingest_sources · create_admin
```

## Démarrage rapide

Le projet est géré avec [`uv`](https://docs.astral.sh/uv/).

```bash
make install   # installe les dépendances (runtime + dev)
cp .env.example .env   # renseigner les variables (aucun secret dans Git)
make up        # démarre les services (docker compose)
make migrate   # applique les migrations
make seed      # amorce 25 Thèmes + programme initial + admin
```

## Commandes de développement

| Cible          | Description                                        |
|----------------|----------------------------------------------------|
| `make install` | Installe les dépendances via `uv`                  |
| `make lint`    | Analyse statique (`ruff`)                          |
| `make type`    | Typage statique (`mypy`)                           |
| `make test`    | Suite de tests (`pytest`)                          |
| `make migrate` | Migrations Alembic (`upgrade head`)                |
| `make seed`    | Amorçage des données                               |
| `make up`      | Démarre les services (`docker compose`)            |
| `make down`    | Arrête les services                                |
