# Implementation Plan: PARTIPOLAI (V1)

_Plan d'implémentation : PARTIPOLAI (V1)_

## Overview

Ce plan convertit la conception de PARTIPOLAI en une série d'étapes de codage incrémentales pour un
**monolithe modulaire** (FastAPI + PostgreSQL 16/pgvector + Redis + Worker Celery + rendu SSR
Jinja2/HTMX/Alpine.js/Tailwind). Chaque tâche s'appuie sur les précédentes et se termine par un
câblage effectif : aucun code orphelin non intégré. L'ordre suit les sprints du cahier des charges
tout en restant granulaire.

Langue d'implémentation : **Python 3.13+** (fixée par le document de conception, style `Mapped`
typé, Pydantic v2, SQLAlchemy 2.x async avec psycopg 3). Les sous-tâches de test sont marquées `*`
et sont optionnelles (le MVP peut les différer), mais chacune des 10 Correctness Properties dispose
d'une sous-tâche PBT dédiée (Hypothesis, ≥ 100 itérations) portant l'étiquette requise.

Arborescence cible : `app/core`, `app/models`, `app/schemas`, `app/api/v1`, `app/services`,
`app/rag`, `app/rag/providers`, `app/workers`, `app/templates`, `app/static` ; `alembic/` ;
`tests/unit`, `tests/integration`, `tests/api`, `tests/rag` ; `scripts/` (seed, ingest_sources,
create_admin) ; `nginx/` ; `docker-compose.yml` ; `Dockerfile`, `Dockerfile.worker`.

## Tasks

- [x] 1. Échafaudage du projet et infrastructure
  - [x] 1.1 Initialiser le projet Python et l'outillage
    - Créer `pyproject.toml` géré par `uv` (FastAPI, Pydantic v2, SQLAlchemy 2.x, psycopg 3,
      Alembic, Celery, redis, Jinja2, argon2/bcrypt, python-jose) et les dépendances de dev (pytest,
      pytest-asyncio, Hypothesis, ruff, mypy)
    - Créer l'arborescence `app/` (`core`, `models`, `schemas`, `api/v1`, `services`, `rag`,
      `rag/providers`, `workers`, `templates`, `static`) et les paquets `tests/`
    - Créer le `Makefile` (cibles install, lint, type, test, migrate, seed, up, down) et
      `.env.example` (variables `DATABASE_URL`, `REDIS_URL`, `JWT_*`, `LLM_PROVIDER`, `LLM_MODEL`,
      `EMBEDDING_PROVIDER`, `EMBEDDING_MODEL`, `EMBEDDING_DIM`, `RAG_*`, `SMTP_*`, `CORS_*`)
    - _Requirements: 33.1, 33.2, 28.6, 32.6_

  - [x] 1.2 Implémenter `app/core/config.py` (configuration par variables d'environnement)
    - `Settings` Pydantic v2 chargeant toutes les variables (base, Redis, JWT, LLM/EMBEDDING/RAG,
      SMTP, CORS) sans secret en dur
    - Rendre fournisseur/modèle de génération et d'embeddings, dimension du vecteur et poids de
      fusion configurables
    - _Requirements: 16.3, 28.6, 33.1_

  - [x] 1.3 Implémenter `app/core/database.py` et `app/core/redis.py`
    - Moteur SQLAlchemy 2.x **async** (psycopg 3), `async_sessionmaker`, dépendance de session
    - Client Redis partagé (cache, broker Celery, limitation de débit)
    - _Requirements: 32.2, 32.3, 33.1_

  - [x] 1.4 Implémenter `app/core/logging.py` (journalisation structurée + request_id)
    - Configuration de logs structurés JSON avec champ `request_id`, réutilisable par API, worker,
      base et RAG
    - _Requirements: 30.1, 26.3_

  - [x] 1.5 Créer le point d'entrée FastAPI et les health checks
    - `app/main.py` assemblant l'application, montage des routeurs `/api/v1` et des pages Web
    - `GET /health` → `{status: ok}` ; `GET /ready` vérifiant PostgreSQL et Redis
    - _Requirements: 27.1, 27.2, 33.1_

  - [x] 1.6 Conteneurisation et orchestration
    - `Dockerfile` (`python:3.13-slim`, utilisateur non-root) pour web ; `Dockerfile.worker` pour le
      Worker Celery
    - `docker-compose.yml` : services web, worker, `postgres` (image `pgvector/pgvector:pg16`),
      redis, nginx, et flower optionnel ; volume de persistance PostgreSQL
    - `nginx/` : HTTPS, en-têtes de sécurité, rate limit de bordure
    - _Requirements: 32.1, 32.2, 32.3, 26.1, 33.1_

  - [x] 1.7 Initialiser Alembic et l'intégration continue
    - `alembic init`, configuration async pointant sur `DATABASE_URL`, activation de l'extension
      `vector`
    - Pipeline CI exécutant `ruff`, `mypy` et `pytest`
    - _Requirements: 32.4, 32.5_

- [x] 2. Couche de données (modèles SQLAlchemy et migrations)
  - [x] 2.1 Modèles noyau : `users`, `themes`, `proposals`, `proposal_versions`
    - Style `Mapped` typé ; `users.email` UNIQUE, `password_hash` ; `themes.slug` UNIQUE ;
      `proposals.slug` UNIQUE, `theme_id` NOT NULL, CHECK `status ∈ {DRAFT, PENDING_REVIEW,
      PUBLISHED, ARCHIVED, REJECTED}`, `version ≥ 1`
    - `proposal_versions` avec `snapshot` jsonb + `change_summary` ; `relationship(lazy="selectin")`
    - _Requirements: 1.1, 1.3, 2.1, 2.5, 3.1, 3.3, 29 (colonnes)_

  - [x] 2.2 Modèles de participation : `votes`, `arguments`, `comments`
    - `votes` : UNIQUE(proposal_id, user_id), CHECK `value ∈ {-1, 0, +1}`
    - `arguments.position ∈ {FOR, AGAINST}` ; `comments.parent_id` auto-référence, `status` défaut
      `VISIBLE`
    - _Requirements: 6.2, 8.1, 9.2, 9.4_

  - [x] 2.3 Modèles documentaires : `sources`, `proposal_sources`, `documents`, `document_chunks`
    - `sources.source_type` contraint aux 7 valeurs ; `proposal_sources.relevance_score` ;
      `documents.checksum` UNIQUE
    - `document_chunks.embedding` de type `VECTOR(EMBEDDING_DIM)` (défaut 1536), `metadata` jsonb
      (source_id, document_id, page, section, publication_date), `token_count`, `chunk_index`
    - _Requirements: 10.1, 10.2, 10.3, 11.3, 11.5, 11.6_

  - [x] 2.4 Modèles programme, équipes, mandat, audit : `programs`, `program_proposals`, `teams`,
        `team_members`, `commitments`, `indicators`, `audit_logs`
    - `program_proposals` porte `priority` et `included_at` ; `commitments.status ∈ {NOT_STARTED,
      IN_PROGRESS, COMPLETED, MODIFIED, ABANDONED}` ; `indicators` (name, unit, baseline, target,
      current_value, source_id) ; `audit_logs.ip_hash` (jamais l'IP brute)
    - _Requirements: 18.4, 20.1, 20.2, 21.1, 21.2, 21.3, 29.1, 29.2_

  - [x] 2.5 Migration Alembic initiale et index de recherche
    - Migration créant toutes les tables ; index HNSW `vector_cosine_ops` sur
      `document_chunks.embedding` ; colonne `tsvector` générée sur `content` + index GIN
    - Vérifier la reproductibilité (`alembic upgrade head` / `downgrade`)
    - _Requirements: 11.6, 12.1, 32.4_

  - [x] 2.6 Scripts d'amorçage : `scripts/seed.py` et `scripts/create_admin.py`
    - Amorcer exactement les 25 Thèmes (slugs définis), un `program` initial, un Administrateur
    - `create_admin` : création d'un compte `is_admin=true` avec mot de passe haché
    - _Requirements: 2.1, 18.1, 1.1, 1.3_

- [x] 3. Authentification et contrôle d'accès
  - [x] 3.1 Implémenter `app/services/auth_service.py`
    - Hachage argon2 (repli bcrypt) ; `password_hash` seul stockage ; émission JWT access + refresh ;
      refresh ; logout (invalidation de session) ; `current_user` sans `password_hash` ; messages
      d'erreur **génériques** à l'inscription et à la connexion
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.6, 1.7, 1.8, 1.9, 26.8_

  - [x] 3.2 Tests unitaires d'`auth_service`
    - Hachage/vérification, émission et rafraîchissement JWT, messages génériques
    - _Requirements: 1.2, 1.3, 1.9_

  - [x] 3.3 Schémas d'auth et endpoints API `app/api/v1/auth.py`
    - `POST /auth/register`, `POST /auth/login` (cookies HttpOnly/Secure/SameSite=Lax côté web),
      `POST /auth/refresh`, `POST /auth/logout`, `GET /auth/me`
    - _Requirements: 1.4, 1.5, 1.6, 1.7, 1.8, 31.1_

  - [x] 3.4 Dépendances d'autorisation `app/api/deps.py`
    - `get_current_user` et `require_admin` ; garde réservant création de Propositions, vote,
      Commentaires et Arguments aux Utilisateurs authentifiés
    - _Requirements: 1.10, 31.3_

  - [x] 3.5 Tests d'API d'authentification
    - Inscription `201` ; email connu ⇒ message générique ; connexion `200` + jetons ; identifiants
      invalides ⇒ `401` générique
    - _Requirements: 1.1, 1.2, 1.4, 1.9_

- [x] 4. Thèmes et Propositions
  - [x] 4.1 Implémenter `app/services/theme_service.py` et l'API Thèmes
    - `list_themes`, `get_theme`, `list_proposals_for_theme` ; `GET /api/v1/themes`,
      `/themes/{id}`, `/themes/{id}/proposals`
    - _Requirements: 2.2, 2.3, 2.4_

  - [x] 4.2 Implémenter `app/services/proposal_service.py` (création, versionnement, archivage)
    - Création à l'état DRAFT, version 1, slug ; `update` créant une `ProposalVersion` complète avec
      `change_summary` et incrément de version, sans écraser l'historique ; `archive` → ARCHIVED ;
      contrôle d'accès auteur/Administrateur ; le tri n'utilise jamais `ORDER BY vote_count`
    - _Requirements: 3.2, 3.4, 3.5, 4.1, 4.5, 4.6, 4.7, 7.3_

  - [x] 4.3 Test de propriété : versionnement monotone et historique immuable
    - **Property 4: Versionnement monotone et historique immuable**
    - **Validates: Requirements 3.4, 3.5**
    - `# Feature: partipolia-platform, Property 4: Versionnement monotone et historique immuable`

  - [x] 4.4 Tests unitaires de `proposal_service`
    - Création DRAFT/slug/version 1, nouvelle version + change_summary, archivage, contrôle d'accès
    - _Requirements: 3.2, 4.1, 4.6, 4.7_

  - [x] 4.5 Schémas et API des Propositions `app/api/v1/proposals.py`
    - `POST /proposals` (DRAFT), `GET /proposals` (filtres theme/status/sort/search/page/limit),
      `GET /proposals/{id}`, `PUT /proposals/{id}`, `DELETE /proposals/{id}` ; validation Pydantic
      renvoyant les champs en erreur
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 31.4_

  - [x] 4.6 Implémenter `app/services/popularity_service.py`
    - `support_count`, `oppose_count`, `participation_count`, `support_rate =
      support/(support+oppose)` (0 si dénominateur nul) ; décomptes absolus toujours renvoyés
    - _Requirements: 7.1, 7.2, 7.4_

  - [x] 4.7 Test de propriété : taux de soutien borné et exclusion du NEUTRE
    - **Property 3: Taux de soutien borné et exclusion du NEUTRE**
    - **Validates: Requirements 7.2**
    - `# Feature: partipolia-platform, Property 3: Taux de soutien borné et exclusion du NEUTRE`

  - [x] 4.7b Pages SSR de restitution des Propositions et Thèmes
    - Gabarits Jinja2/HTMX/Alpine/Tailwind : fiche `/propositions/{slug}` (titre, problème,
      proposition, pourquoi, décomptes pour/contre, coût, financement, arguments, sources, débat,
      action IA), consultation sans authentification ; affichage du nombre absolu de votes à côté de
      tout pourcentage
    - _Requirements: 24.1, 24.4, 7.4, 25.1_

- [x] 5. Vote, Arguments, Commentaires et modération simple
  - [x] 5.1 Implémenter `app/services/vote_service.py` (UPSERT/retrait/décomptes)
    - `cast` UPSERT sur UNIQUE(proposal_id, user_id) avec `value ∈ {+1,0,-1}` ; `withdraw` (DELETE) ;
      `counts`
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [x] 5.2 Test de propriété : unicité et idempotence du Vote
    - **Property 1: Unicité et idempotence du Vote**
    - **Validates: Requirements 6.2, 6.3**
    - `# Feature: partipolia-platform, Property 1: Unicité et idempotence du Vote`

  - [x] 5.3 Test de propriété : round-trip de retrait de Vote
    - **Property 2: Round-trip de retrait de Vote**
    - **Validates: Requirements 6.4**
    - `# Feature: partipolia-platform, Property 2: Round-trip de retrait de Vote`

  - [x] 5.4 API des Votes `app/api/v1/votes.py`
    - `POST /proposals/{id}/votes`, `DELETE /proposals/{id}/votes`, `GET /proposals/{id}/votes` ;
      refus `401` si non authentifié
    - _Requirements: 6.1, 6.4, 6.5, 6.6_

  - [x] 5.5 Implémenter `app/services/argument_service.py` et l'API Arguments
    - Création `FOR`/`AGAINST`, lecture, modification, suppression ; contrôle auteur/Administrateur ;
      `POST /proposals/{id}/arguments`, `PUT /arguments/{id}`, `DELETE /arguments/{id}`
    - _Requirements: 8.1, 8.2, 8.3_

  - [x] 5.6 Implémenter `app/services/audit_service.py`
    - `record(action, entity_type, entity_id, old_data, new_data, ip_hash)` ; `ip_hash` uniquement
    - _Requirements: 29.1, 29.2_

  - [x] 5.7 Implémenter `app/services/moderation_service.py` (Moteur_De_Modération)
    - Filtre automatique → classification `conforme|douteux` ; « conforme » ⇒ `VISIBLE`, « douteux »
      ⇒ File_De_Modération sans publication ; consignation de chaque décision dans `audit_logs`
    - _Requirements: 17.1, 17.2, 17.3, 17.4_

  - [x] 5.8 Implémenter `app/services/comment_service.py` et l'API Commentaires
    - `create` (→ modération), `list_thread` (parent_id), `update`, `delete` (auteur/Administrateur) ;
      `GET`/`POST /proposals/{id}/comments`, `PUT`/`DELETE /comments/{id}` ; refus `401` si non
      authentifié
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5_

  - [x] 5.9 Tests unitaires de `vote_service` et contrôle d'accès Arguments/Commentaires
    - UPSERT/unicité/retrait/décomptes ; `403` pour non-auteur sur arguments/commentaires
    - _Requirements: 6.2, 6.3, 6.4, 8.3, 9.3_

- [x] 6. Sources et pipeline d'ingestion documentaire
  - [x] 6.1 Implémenter `app/services/source_service.py` et l'API Sources
    - `create` (Administrateur), `attach_to_proposal` (relevance_score), `list_sources` ;
      `GET /sources`, `POST /sources`, `POST /proposals/{id}/sources`
    - _Requirements: 10.1, 10.2, 10.3, 10.4_

  - [x] 6.2 Définir les Protocols Providers IA `app/rag/providers/`
    - `LLMProvider.generate(messages, temperature)`, `EmbeddingProvider.embed(texts)` ;
      implémentations `openai/`, `anthropic/`, `local/` sélectionnées par configuration ; isolation
      du domaine
    - _Requirements: 16.1, 16.2, 16.3, 16.4_

  - [x] 6.3 Implémenter les modules d'ingestion `app/rag/` (téléchargement → chunking)
    - `Downloader.fetch`, `FormatDetector.detect` (HTML/PDF/TXT/CSV), `TextExtractor.extract`,
      `TextCleaner.clean`, `Deduplicator.checksum/is_known` (documents.checksum UNIQUE),
      `Chunker.chunk` (800 tokens, chevauchement 120, métadonnées complètes)
    - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

  - [x] 6.4 Test de propriété : idempotence de l'ingestion par checksum
    - **Property 5: Idempotence de l'ingestion par checksum**
    - **Validates: Requirements 11.3**
    - `# Feature: partipolia-platform, Property 5: Idempotence de l'ingestion par checksum`

  - [x] 6.5 Test de propriété : invariants de chunking et préservation des métadonnées
    - **Property 6: Invariants de chunking et préservation des métadonnées**
    - **Validates: Requirements 11.4, 11.5**
    - `# Feature: partipolia-platform, Property 6: Invariants de chunking et préservation des métadonnées`

  - [x] 6.6 Tâches Celery d'ingestion `app/workers/`
    - `ingest_document` orchestrant `extract_document` → `chunk_document` → `generate_embeddings`,
      plus `reindex_document` ; stockage des embeddings dans `document_chunks` (VECTOR + HNSW) ;
      réessai avec backoff, idempotence par checksum ; API non bloquée
    - _Requirements: 11.1, 11.6, 23.1, 23.2, 23.3_

  - [x] 6.7 Endpoint d'ingestion et script `scripts/ingest_sources.py`
    - `POST /api/v1/admin/documents/ingest` (Administrateur) émettant la tâche Celery (`202`) ;
      script d'ingestion en lot par URL
    - _Requirements: 11.1, 23.2, 23.3_

- [x] 7. Recherche hybride et Assistant IA/RAG
  - [x] 7.1 Implémenter `HybridSearch` et `ScoreFusion` (`app/rag/`)
    - Recherche lexicale tsvector/tsquery + sémantique pgvector (distance cosinus) ; fusion
      configurable `0.40 sem + 0.30 lex + 0.20 qualité + 0.10 récence`
    - _Requirements: 12.1, 12.2_

  - [x] 7.2 Test de propriété : somme des poids de fusion égale à 1.0
    - **Property 8: Somme des poids de fusion égale à 1.0**
    - **Validates: Requirements 12.2**
    - `# Feature: partipolia-platform, Property 8: Somme des poids de fusion égale à 1.0`

  - [x] 7.3 Implémenter `Reranker` (`app/rag/`)
    - Reranking réduisant les 20 meilleurs à 6, ordonnés par score décroissant (`min(6, taille)`)
    - _Requirements: 12.3_

  - [x] 7.4 Test de propriété : reranking — exactement 6 parmi les 20 meilleurs
    - **Property 9: Reranking — exactement 6 parmi les 20 meilleurs**
    - **Validates: Requirements 12.3**
    - `# Feature: partipolia-platform, Property 9: Reranking — exactement 6 parmi les 20 meilleurs`

  - [x] 7.5 Implémenter `QuestionClassifier`, `QueryRewriter`, `ContextBuilder`, `CitationValidator`
    - Classification (GENERAL/ON_PROPOSITION/DOCUMENTARY/COMPARATIVE), réécriture de requête,
      construction de contexte avec citations numérotées, validation détectant les affirmations
      factuelles non sourcées
    - _Requirements: 13.2, 13.3, 13.4, 14.3, 14.4, 14.5_

  - [x] 7.6 Implémenter `RagPipeline` et le prompt système avec garde-fous
    - Enchaînement classification → réécriture → recherche hybride → fusion → reranking → contexte →
      génération LLM (faible température) → validation → réponse `{answer, sources[], confidence}` ;
      retrieval obligatoire avant génération ; « information insuffisante » si aucun passage ;
      distinction faits/estimations/opinions/hypothèses/désaccords ; pas d'invention de nombres ;
      pas de recommandation de vote individuelle ; comparaison sur critères factuels ; aucune
      écriture dans `votes`/`program_proposals`
    - _Requirements: 13.3, 13.5, 13.6, 13.7, 13.8, 13.9, 13.10, 14.1, 14.2, 14.6, 15.1, 15.2, 15.3_

  - [x] 7.7 Test de propriété : ancrage documentaire des réponses RAG
    - **Property 7: Ancrage documentaire des réponses RAG**
    - **Validates: Requirements 13.5, 13.7, 13.8, 13.9, 14.1, 14.6**
    - `# Feature: partipolia-platform, Property 7: Ancrage documentaire des réponses RAG`
    - Providers IA simulés (mocks)

  - [x] 7.8 Test de propriété : découplage IA — non-écriture dans les tables de décision
    - **Property 10: Découplage IA — non-écriture dans les tables de décision**
    - **Validates: Requirements 15, 18, 19**
    - `# Feature: partipolia-platform, Property 10: Découplage IA — non-écriture dans les tables de décision`
    - Providers IA simulés (mocks)

  - [x] 7.9 Endpoint chat, journalisation RAG et détection de doublons
    - `POST /api/v1/chat {message, proposal_id}` → `{answer, sources[], confidence}` ; journalisation
      de chaque réponse (request_id, user_id, timestamp, modèle, retrieved_documents) ;
      `app/services/duplicate_detection_service.py` (`find_similar`) branché à `POST /proposals`
      avec actions Consulter/Créer quand même/Améliorer
    - _Requirements: 13.1, 14.7, 30.2, 5.1, 5.2, 5.3_

  - [x] 7.10 Tests unitaires `rag_retriever` et `ranking`
    - Recherche hybride, contexte, « information insuffisante » ; fusion (poids = 1.0), reranking
      20 → 6, ordre décroissant
    - _Requirements: 12.1, 12.2, 12.3, 13.8_

- [x] 8. Programme, équipes, mandat et statistiques
  - [x] 8.1 Implémenter `app/services/program_service.py` et l'API Programme
    - `get_program`, `list_program_proposals`, `add_proposal(priority)`, `remove_proposal`,
      `statistics`, `build_draft_view` (« Programme en construction » : fortement soutenues +
      documentées + non contradictoires + estimées, calcul non décisionnel) ; `priority`/`included_at`
      attributs de l'association ; inclusion définitive explicite et traçable
    - `GET /program`, `GET /program/proposals`, `POST /program/proposals`,
      `DELETE /program/proposals/{id}`, `GET /program/statistics`
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5, 19.1, 19.2, 19.3_

  - [x] 8.2 Tests unitaires de `program_service`
    - Ajout/retrait d'association (priority, included_at), statistiques, vue « Programme en
      construction » non décisionnelle
    - _Requirements: 18.2, 18.3, 19.1, 19.2_

  - [x] 8.3 Implémenter `TeamService`, `MandateService` et leurs API
    - Équipes/membres (role, bio) ; engagements (status NOT_STARTED..ABANDONED, target_date,
      progress, notes) ; indicateurs (name, unit, baseline, target, current_value, source_id) ;
      `GET /teams`, `GET /mandate/commitments`, `GET /mandate/indicators` ; enregistrement des
      modifications
    - _Requirements: 20.1, 20.2, 20.3, 21.1, 21.2, 21.3, 21.4_

  - [x] 8.4 Implémenter `app/services/statistics_service.py` et l'API statistiques
    - `global_counts` (users, proposals, votes, comments, sources, themes) agrégés/anonymisés ;
      `GET /api/v1/statistics` ; tâche Celery `recalculate_statistics` ; aucune statistique
      individuelle reconstituant les opinions
    - _Requirements: 22.1, 22.2, 22.3, 23.1_

  - [x] 8.5 Page SSR du Programme avec hypothèses financières
    - Gabarit `/programme` : liste des Thèmes ; par mesure, nombre de propositions, votes, soutien
      agrégé, coûts/recettes/économies estimés ; indication claire des hypothèses des agrégats
      financiers ; consultation sans authentification
    - _Requirements: 24.2, 24.3, 24.4, 25.1_

- [x] 9. Checkpoint — Assurer que tous les tests passent
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Sécurité, conformité et durcissement
  - [x] 10.1 Middleware de sécurité et gestion globale des exceptions `app/core/`
    - `RequestIdMiddleware` (X-Request-ID), `SecurityHeadersMiddleware` (X-Content-Type-Options,
      X-Frame-Options, Referrer-Policy, Content-Security-Policy), CORS, gestionnaire global
      d'exceptions produisant le modèle d'erreur normalisé (codes 400/401/403/404/409/429/500)
    - _Requirements: 26.2, 26.3, 26.6, 26.7, 31.2, 31.3, 31.4_

  - [x] 10.2 Limitation de débit basée sur Redis
    - `RateLimiter` appliqué à login/register/chat/proposals/comments/votes avec protection renforcée
      de `POST /api/v1/chat` ; en-tête `Retry-After` sur `429`
    - _Requirements: 26.4, 26.5_

  - [x] 10.3 Pages et fonctions RGPD
    - Pages `/mentions-legales`, `/confidentialite`, `/cookies`, `/conditions` ; export des données,
      suppression de compte, gestion du consentement ; politique de rétention explicite (comptes,
      votes, journaux, conversations IA, signalements) ; vérification de l'absence de collecte de
      données superflues et de profil politique individuel
    - _Requirements: 28.1, 28.2, 28.3, 28.4, 28.5, 30.3_

- [x] 11. Tests d'API, d'intégration et évaluation RAG
  - [x] 11.1 Tests d'API des Propositions, Votes et Commentaires
    - CRUD Proposition (`201` + détection de doublons, liste filtrée, nouvelle version, archivage,
      `403` non-auteur) ; vote UPSERT/DELETE/décomptes (`401` si anonyme) ; commentaires →
      modération, fils via parent_id
    - _Requirements: 4.1, 4.3, 4.5, 4.6, 4.7, 6.1, 6.4, 9.1, 9.2_

  - [x] 11.2 Tests d'API du chat RAG
    - `POST /api/v1/chat` renvoie `{answer, sources[], confidence}` ; protection de débit renforcée
    - _Requirements: 13.1, 26.5_

  - [x] 11.3 Dataset et évaluation du Pipeline RAG (`tests/rag/`)
    - Jeu d'évaluation (question, expected_documents, expected_answer, expected_citations) ; métriques
      de précision/rappel de récupération, exactitude des citations, ancrage de la réponse ; cas sans
      passage pertinent ⇒ « information insuffisante »
    - _Requirements: 13.5, 13.7, 14.1, 14.6, 32.5_

  - [x] 11.4 Tests d'intégration d'infrastructure et santé
    - `docker compose up` démarre l'ensemble des services ; persistance PostgreSQL ; Redis
      fonctionnel ; `GET /health` et `GET /ready` ; migrations reproductibles ; absence de secret
      dans Git
    - _Requirements: 27.1, 27.2, 32.1, 32.2, 32.3, 32.4, 32.6_

- [x] 12. Checkpoint final — Assurer que tous les tests passent
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Les sous-tâches marquées `*` sont optionnelles et peuvent être ignorées pour un MVP plus rapide.
- Chaque tâche référence les Exigences précises qu'elle implémente, pour la traçabilité.
- Les 10 Correctness Properties disposent chacune d'une sous-tâche PBT dédiée (Hypothesis, ≥ 100
  itérations) placée au plus près de l'implémentation, avec l'étiquette
  `# Feature: partipolia-platform, Property N: …`.
- Les propriétés RAG (7 et 10) utilisent des Providers IA simulés pour isoler la logique du coût des
  appels externes.
- Les checkpoints (tâches 9 et 12) assurent une validation incrémentale.
- Aucun composant IA/RAG n'écrit dans les tables de décision (`votes`, `program_proposals`), ce qui
  matérialise le principe directeur (l'IA documente et assiste, ne vote pas, ne décide pas).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "1.6", "1.7"] },
    { "id": 3, "tasks": ["2.1", "2.2", "2.3", "2.4"] },
    { "id": 4, "tasks": ["2.5"] },
    { "id": 5, "tasks": ["2.6", "3.1", "6.2"] },
    { "id": 6, "tasks": ["3.2", "3.3", "3.4"] },
    { "id": 7, "tasks": ["3.5", "4.1", "4.2", "4.6", "5.1", "5.6", "6.1", "6.3"] },
    { "id": 8, "tasks": ["4.3", "4.4", "4.7", "5.2", "5.3", "5.5", "5.7", "6.4", "6.5", "7.1", "7.3"] },
    { "id": 9, "tasks": ["4.5", "5.4", "5.8", "6.6", "7.2", "7.4", "7.5"] },
    { "id": 10, "tasks": ["4.7b", "5.9", "6.7", "7.6", "8.1", "8.3", "8.4"] },
    { "id": 11, "tasks": ["7.7", "7.8", "7.9", "8.2", "8.5"] },
    { "id": 12, "tasks": ["7.10", "10.1", "10.3"] },
    { "id": 13, "tasks": ["10.2"] },
    { "id": 14, "tasks": ["11.1", "11.2", "11.3", "11.4"] }
  ]
}
```
