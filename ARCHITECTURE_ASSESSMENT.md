# Partipolia - Architecture Assessment and Migration Strategy (Phase 0)

Status: Phase 0 deliverable. Scope of the transformation: Phases 0 to 3 of the product spec.

## 1. Baseline

| Item | Finding |
|------|---------|
| Stack | Python 3.13, FastAPI, Pydantic v2, async SQLAlchemy 2, psycopg 3, PostgreSQL 16 + pgvector, Redis, Celery, Jinja2/HTMX/Alpine/Tailwind, Alembic, Docker Compose + nginx |
| Layout | `app/{core,models,schemas,api/v1,services,rag,workers,web,templates,static}`, `scripts/`, `tests/{unit,integration,api,rag}` |
| Migrations | `0001` to `0005` (initial schema, consents, team display fields, founding members seed, "repair the law" tables) |
| Test baseline | `428 passed, 1 failed, 4 skipped` (WSL, `.venv`) |
| Known failure | `tests/rag/test_pipeline_decoupling_property.py::test_rag_pipeline_only_reads_document_chunks` already fails before any change (the fake session records no SQL). Not related to this work. |
| Skipped | 4 tests that need Docker or a live PostgreSQL (`TEST_DATABASE_URL`) |

Tests that need PostgreSQL are skipped locally, so new DB-backed tests must also work with pure unit tests (engine) and with the existing skip pattern.

## 2. Inventory

### Reusable (kept, evolved)
- `app/core/*`: config, database, redis, rate limiting, request-id and security-headers middleware, structured logging, normalized error handlers.
- `app/main.py`: lifespan, `/health`, `/ready`, static mount, router mounting.
- `app/models/user.py` + `mixins.py` + `audit.py`; `services/auth_service.py`, `privacy_service.py`, `audit_service.py`; `api/v1/auth.py`, `privacy.py`; `api/deps.py`.
- Templates infrastructure (`web/router.py` `templates`, `ssr_context`, `rgpd/*` pages).
- Docker, nginx, Celery worker, Makefile, CI workflow, Alembic setup.
- `app/rag/*` and `models/{document,source}.py`: dormant, optional future capability.

### Legacy (archived and hidden)
- Models: `proposal, program, theme, vote, argument, comment, mandate, team, legal_problem, legal_problem_reform, legal_analysis, side_effect_report`.
- Services: matching services for the above plus `popularity`, `duplicate_detection`, `moderation`, `statistics`.
- API routers: `themes, proposals, sources, documents, arguments, votes, statistics, program, teams, mandate, chat, legal_problems`.
- Web pages: `programme, themes, propositions, statistiques, equipes, mandat, assistant, reparer_la_loi*` and their templates.

## 3. Migration strategy: archive and hide

1. **No deletions of code or tables.** Legacy modules stay importable; no migration drops anything.
2. **Hide at the edge.** The legacy API routers are no longer included in `api_router`; the legacy web routes move to `app/web/legacy_router.py`, which is not mounted. Navigation and templates exposed to users are replaced by Partipolia ones.
3. **Tests.** Legacy tests that exercise the legacy routers mount those routers on their own test apps (verified at Task 2); tests that depend on the removed public routes are marked `legacy` and kept runnable with `-m legacy`.
4. **New code lives beside the old.** New modules: `app/economics/` (pure engine), new models/schemas/services/api/web files named after the spec. No legacy file is edited except to unhook it.
5. **Users.** Add `users.role` (enum). `is_admin` stays as a compatibility column: migration sets `role='super_admin'` where `is_admin` is true. New code checks `role`; legacy code keeps reading `is_admin`.
6. **Rollback.** All migrations are additive and have a `downgrade`.

## 4. Target architecture

```
app/economics/          Pure Python, no FastAPI / SQLAlchemy imports
  decimal_utils.py      Decimal helpers + rounding policy
  types.py              ModelSnapshot, ParameterRange, results (frozen dataclasses)
  direct.py             VAT (TTC -> VAT), excise
  input_output.py       Leontief (A38), deterministic precision
  employment.py         Elasticities, horizons, contributions, benefits
  fuel.py               Avoided trips, TICPE, VAT on fuel
  engine.py             Orchestrator: low / central / high, net adjustments
app/services/           Load ModelSnapshot, persist results, aggregate
app/api/v1/             simulations, commitments, dashboard, economic_data, admin
app/web/                Partipolia public pages (landing, simulator, dashboard, methodology)
```

Double-counting rules (enforced in code and tests):
- Direct: VAT on final TTC spend plus category excise.
- Indirect: Leontief output change; only production taxes and corporate tax on lost value added. VAT on intermediates excluded (deductible); labour taxes excluded.
- Induced: labour flows only (contributions, income tax, unemployment benefits).
- Fuel: avoided trips attributed once through one per-category travel parameter.
- Net = direct + indirect + induced - additional public expenditure +/- rebound, with substitution, import leakage and re-spend shares applied.

Reproducibility: published `ModelVersion` rows are immutable and reference exact `EconomicDataset` versions; each `EconomicSimulation` stores the model and dataset version ids. The engine is a pure function of (snapshot, input).

## 5. Technical risks

| Risk | Mitigation |
|------|-----------|
| Official A38 TES and elasticities may not map cleanly to the six categories | Explicit category-to-sector mapping table; gaps recorded in `DATA_GAPS.md` as provisional parameters with low confidence |
| Leontief inverse with floats breaks determinism | Fixed-precision `Decimal` Gauss-Jordan elimination with documented precision and rounding |
| Pre-existing failing RAG test | Tracked, not fixed here; excluded from regression comparison |
| Hiding legacy routers breaks legacy tests | Tests mount routers locally or are marked `legacy` |
| `is_admin` / `role` divergence | Both written together by one helper; migration test |
| DB-backed tests skipped locally | Engine fully unit-testable; DB logic covered where PostgreSQL is available |
| Political-content sensitivity | Mandatory disclaimers; commitments labelled "declared"; aggregates behind a minimum cohort threshold |
| No network assumptions about official data | Datasets are versioned files with source URL, date and checksum; imports validated before publish |
