# PARTIPOLAI — Makefile (outillage `uv`)
# Cibles : install, lint, type, test, migrate, seed, up, down

.DEFAULT_GOAL := help
.PHONY: help install lint type test migrate seed seed-model css up down fmt revision downgrade

## install : installe les dépendances (runtime + dev) via uv
install:
	uv sync --all-extras --dev

## lint : analyse statique du code avec ruff
lint:
	uv run ruff check app tests scripts
	uv run ruff format --check app tests scripts

## fmt : formate le code avec ruff
fmt:
	uv run ruff format app tests scripts
	uv run ruff check --fix app tests scripts

## type : vérification de typage avec mypy
type:
	uv run mypy app

## test : exécute la suite de tests (pytest)
test:
	uv run pytest

## migrate : applique les migrations Alembic jusqu'à head
migrate:
	uv run alembic upgrade head

## revision : génère une migration Alembic (m="message")
revision:
	uv run alembic revision --autogenerate -m "$(m)"

## downgrade : annule la dernière migration Alembic
downgrade:
	uv run alembic downgrade -1

## seed : amorce les données historiques (thèmes, programme, administrateur)
seed:
	uv run python -m scripts.seed

## seed-model : publie la version initiale du modèle économique Partipolia (idempotent)
seed-model:
	uv run python -m scripts.seed_economic_model

## css : recompile la feuille de style Tailwind (binaire autonome dans tools/)
css:
	./tools/tailwindcss -i tools/tailwind.input.css -o app/static/css/partipolia.css --minify

## up : démarre l'ensemble des services (docker compose)
up:
	docker compose up -d --build

## down : arrête les services (conserve les volumes)
down:
	docker compose down

## help : affiche cette aide
help:
	@grep -E '^##' $(MAKEFILE_LIST) | sed -e 's/## //'
