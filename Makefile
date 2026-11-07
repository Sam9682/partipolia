# PARTIPOLAI — Makefile (outillage `uv`)
# Cibles : install, lint, type, test, migrate, seed, up, down

.DEFAULT_GOAL := help
.PHONY: help install lint type test security migrate seed seed-model css loadtest loadtest-smoke perf-test up down fmt revision downgrade

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

## security : audit des dépendances (pip-audit), analyse statique (bandit), secrets (detect-secrets) + tests/security
# Rapports écrits dans reports/security/. Échec si vulnérabilité, résultat bandit
# de sévérité >= moyenne, ou secret absent de .secrets.baseline.
# Exceptions pip-audit justifiées (aucun correctif amont ; algorithmes JWT restreints
# à HS256 avec clé symétrique, ecdsa non utilisé directement) :
#   PYSEC-2026-4298 (python-jose), PYSEC-2026-1325 (ecdsa, attaque Minerva sur signature ECDSA).
PIP_AUDIT_IGNORES = --ignore-vuln PYSEC-2026-4298 --ignore-vuln PYSEC-2026-1325
security:
	@mkdir -p reports/security
	uv export --frozen --all-extras --no-emit-project --no-hashes -o reports/security/requirements.txt > /dev/null
	uv run pip-audit -r reports/security/requirements.txt --no-deps --disable-pip $(PIP_AUDIT_IGNORES) -f json -o reports/security/pip-audit.json
	uv run bandit -q -r app scripts -ll -f json -o reports/security/bandit.json
	./scripts/check_secrets.sh
	uv run pytest tests/security

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

## loadtest : exécute les 4 profils de charge (code non nul si un profil ne démarre pas) ; voir docs/perf/environment.md
loadtest:
	uv run python -m loadtest.run --profiles public_read,compute,auth_write,participation_spike
## loadtest-smoke : profil fumée (20 VU, 60 s) avec vérification des seuils
loadtest-smoke:
	uv run python -m loadtest.run --profiles smoke --check
## perf-test : tests de performance d'agrégation (marqueur pytest `perf`, exclus de `make test`)
perf-test:
	uv run pytest tests/perf -m perf
## up : démarre l'ensemble des services (docker compose)
up:
	docker compose up -d --build

## down : arrête les services (conserve les volumes)
down:
	docker compose down

## help : affiche cette aide
help:
	@grep -E '^##' $(MAKEFILE_LIST) | sed -e 's/## //'
