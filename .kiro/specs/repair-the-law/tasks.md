# Implementation Plan

## Plan d'implémentation — « Réparer la loi » (MVP)

## Overview

Ce plan décompose la conception de « Réparer la loi » en tâches de codage incrémentales pour un
agent de génération de code. Chaque tâche s'appuie sur les précédentes et se termine par une
intégration au reste du monolithe modulaire de `partipolia-platform`, sans code orphelin.

L'ordre suit la stratification de la conception : **modèles + migration Alembic → schémas Pydantic →
services (`legal_reform_service`, `legal_analysis_service`) avec tests unitaires et PBT → routeur
API REST avec tests API → tâches Celery → pages SSR → script de seed → tests d'intégration**.

Conventions du dépôt respectées : SQLAlchemy 2.x style `Mapped`, `relationship(lazy="selectin")`,
Pydantic v2, FastAPI, migrations Alembic **additives**, tests `pytest`/`pytest-asyncio` et propriétés
`Hypothesis` (fichiers `tests/unit/*_property.py`). Outillage : `make lint` (ruff), `make type`
(mypy), `make test` (pytest), `make migrate` (alembic). Interface et API en français.

Chaque propriété PBT est implémentée par **un unique** test Hypothesis à **≥ 100 itérations**, avec
Providers IA simulés (mocks) pour les propriétés RAG, et porte une étiquette
`# Feature: repair-the-law, Property N: ...`.

## Tasks

- [x] 1. Modèles de données et migration Alembic additive
  - [x] 1.1 Créer le modèle `LegalProblem`
    - Créer `app/models/legal_problem.py` avec la table `legal_problems` (style `Mapped`, `TimestampMixin`)
    - Champs : `slug` (UNIQUE, index), `title`, `summary`, `theme_id` (FK `themes.id`, `ondelete=RESTRICT`, NOT NULL, index), `affected_citizens_count` (Integer, défaut 0), `concerned_legal_texts` (JSONB liste), `jurisprudence_refs` (JSONB liste), `complexity_level` (String 16), `status` (défaut `PUBLISHED`)
    - CHECK `complexity_level IN ('FAIBLE','MOYEN','ELEVE')` et CHECK `affected_citizens_count >= 0`
    - Relation `reform_links` vers `LegalProblemReform` (`cascade="all, delete-orphan"`, `lazy="selectin"`)
    - Enregistrer le modèle dans `app/models/__init__.py`
    - _Requirements: 1.1, 2.1, 2.2, 2.4, 2.6_

  - [x] 1.2 Créer le modèle d'association `LegalProblemReform`
    - Créer `app/models/legal_problem_reform.py` avec la table `legal_problem_reforms` (`CreatedAtMixin`)
    - Champs : `problem_id` (FK `legal_problems.id`, `ondelete=CASCADE`, index), `proposal_id` (FK `proposals.id`, `ondelete=RESTRICT`, index), `is_status_quo` (Boolean, défaut False)
    - Contrainte `UniqueConstraint("problem_id","proposal_id")` (`uq_lpr_problem_proposal`)
    - Index partiel `UNIQUE(problem_id) WHERE is_status_quo` (`uq_lpr_one_status_quo`, `postgresql_where=text("is_status_quo")`)
    - Relations `problem` (back_populates `reform_links`) et `proposal`, en `lazy="selectin"`
    - Enregistrer le modèle dans `app/models/__init__.py`
    - _Requirements: 2.5, 4.1, 4.3, 13.1_

  - [x] 1.3 Créer le modèle `SideEffectReport`
    - Créer `app/models/side_effect_report.py` avec la table `side_effect_reports` (`TimestampMixin`)
    - Champs : `proposal_id` (FK `proposals.id`, `ondelete=CASCADE`, index), `author_id` (FK `users.id`, `ondelete=CASCADE`, index), `content` (Text), `status` (String 16, défaut `PENDING`)
    - CHECK `status IN ('PENDING','VISIBLE','REJECTED')` et CHECK `char_length(content) <= 5000`
    - Relations `proposal` et `author` en `lazy="selectin"` ; enregistrer dans `app/models/__init__.py`
    - _Requirements: 8.8, 8.9_

  - [x] 1.4 Créer le modèle `LegalAnalysis` (matérialisation des analyses IA)
    - Créer `app/models/legal_analysis.py` avec la table `legal_analyses` (`TimestampMixin`)
    - Champs : `proposal_id` (FK `proposals.id`, `ondelete=CASCADE`, index), `kind` (String 32), `status` (String 16, défaut `PENDING`), `answer` (Text nullable), `payload` (JSONB nullable), `confidence` (Float, défaut 0.0), `doc_version` (String 64 nullable)
    - Contrainte `UniqueConstraint("proposal_id","kind")` (`uq_legal_analyses_proposal_kind`)
    - CHECK `kind IN (...9 agents...)`, CHECK `status IN ('PENDING','READY','INDISPONIBLE')`, CHECK `confidence >= 0 AND confidence <= 1`
    - Relation `proposal` en `lazy="selectin"` ; enregistrer dans `app/models/__init__.py`
    - _Requirements: 3.1, 5.1, 6.1, 7.1, 12.6_

  - [x] 1.5 Générer la migration Alembic additive
    - Créer une révision Alembic créant, dans l'ordre, `legal_problems`, `legal_problem_reforms`, `side_effect_reports`, `legal_analyses` avec toutes les contraintes/index ci-dessus
    - `downgrade` supprime les tables dans l'ordre inverse ; aucune altération des tables existantes
    - Vérifier via `make migrate` (upgrade puis downgrade) sur une base de test
    - _Requirements: 2.2, 2.5, 2.6, 4.2, 8.9, 12.6, 13.1_

- [x] 2. Checkpoint — modèles et migration
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Schémas Pydantic v2 « Réparer la loi »
  - [x] 3.1 Créer les schémas de Problème Juridique
    - Créer `app/schemas/legal_problem.py` : `LegalProblemCreate` (validation `complexity_level ∈ {FAIBLE,MOYEN,ELEVE}`), `LegalProblemSummary`, `LegalProblemDetail`, `ReformView` (avec marqueur statu quo)
    - Réutiliser le schéma de pagination `common.py` (défaut 20, max 100, `total`)
    - _Requirements: 1.3, 1.4, 2.1, 2.2, 2.3, 4.4, 14.1_

  - [ ]* 3.2 Écrire le test de propriété — validation du Niveau_De_Complexité
    - **Property 4 : Validation du Niveau_De_Complexité**
    - **Validates: Requirements 2.2, 2.3**
    - Cible : validation `LegalProblemCreate` (accepté ssi ∈ {FAIBLE,MOYEN,ELEVE})

  - [x] 3.3 Créer les schémas d'actions et d'analyses
    - Dans `app/schemas/legal_problem.py` : `VoteInput` (`value ∈ {-1,0,+1}`), `VoteCounts` (`support_count`, `oppose_count`, `participation_count`), `PositionInput` (`position ∈ {FOR,AGAINST}`), `AmendmentInput` (contenu 1..5000), `SideEffectInput` (contenu 1..5000), `ExplainInput`
    - Schémas de sortie IA : `AnalysisResult`, `Unavailable`, `SimulationResult`, `ReformAnalysisBundle`, `TechnicalConclusion` (avec `sources[]`, `markers[]`, horizons, `risks[]`)
    - _Requirements: 4.7, 5.2, 5.5, 6.2, 6.3, 8.9, 9.1, 11.6, 13.3, 13.4, 14.5_

  - [ ]* 3.4 Écrire le test de propriété — bornes de longueur amendements/signalements
    - **Property 6 : Bornes de longueur des Amendements et Signalements**
    - **Validates: Requirements 8.9**
    - Cible : validation `AmendmentInput` / `SideEffectInput` (accepté ssi 1..5000)

- [x] 4. Implémenter `legal_reform_service` (orchestration domaine)
  - [x] 4.1 Créer le squelette du service et la lecture des Problèmes Juridiques
    - Créer `app/services/legal_reform_service.py` avec `LegalReformService(session)`
    - Méthodes : `list_problems(page, limit)` (pagination bornée + `total`), `get_problem(id)` (404 si absent), `get_problem_by_slug(slug)`, `create_problem(admin, data)`
    - Générer un `slug` unique et un `id` unique à la création
    - _Requirements: 1.1, 1.4, 1.5, 1.6, 2.1, 2.4, 2.6, 14.1, 14.2, 14.3_

  - [ ]* 4.2 Écrire le test de propriété — unicité identifiant et slug
    - **Property 5 : Unicité de l'identifiant et du slug d'un Problème_Juridique**
    - **Validates: Requirements 2.6**

  - [x] 4.3 Implémenter le rattachement des Réformes et la validation de cardinalité
    - Méthodes `attach_reform(admin, problem_id, proposal_id, is_status_quo)`, `list_reforms(problem_id)` (inclut statu quo), `_validate_cardinality(reforms)`
    - `_validate_cardinality` : `3 ≤ len ≤ 5` **et** `count(is_status_quo) == 1`, sinon `ReformCardinalityError` (→ 422), état inchangé
    - Rattachement via l'association `legal_problem_reforms` sans modifier `Proposal`
    - _Requirements: 2.5, 4.1, 4.2, 4.3, 4.4, 13.1_

  - [ ]* 4.4 Écrire le test de propriété — cardinalité des Réformes
    - **Property 3 : Cardinalité des Réformes_Proposées**
    - **Validates: Requirements 2.5, 4.1, 4.2**

  - [x] 4.5 Implémenter le vote citoyen et les décomptes (délégation)
    - `cast_vote(user, problem_id, reform_id, value)` déléguant à `vote_service` (UPSERT ±1/0, unicité `(proposal_id,user_id)`)
    - `reform_vote_counts(problem_id, reform_id)` déléguant à `popularity_service`, exposant explicitement `participation_count = support_count + oppose_count` (NEUTRE exclu) dans `VoteCounts`
    - Aucun tri `ORDER BY vote_count` ; utiliser le score de `popularity_service` si un ordre est requis
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 9.1, 9.2, 9.6, 13.2, 13.8_

  - [ ]* 4.6 Écrire le test de propriété — unicité et idempotence du vote
    - **Property 1 : Unicité et idempotence du Vote_Citoyen**
    - **Validates: Requirements 8.1, 8.2, 8.3, 8.4, 13.2**

  - [ ]* 4.7 Écrire le test de propriété — participation = support + oppose
    - **Property 2 : participation_count = support_count + oppose_count**
    - **Validates: Requirements 9.1, 9.3**

  - [x] 4.8 Implémenter amendements, prises de position et signalements (délégation)
    - `propose_amendment(user, problem_id, reform_id, data)` déléguant à `proposal_service` (nouvelle `ProposalVersion`, incrément +1, snapshot/change_summary/edited_by, versions antérieures préservées)
    - `list_versions(problem_id, reform_id)` ordonné par version croissante
    - `submit_position(user, problem_id, reform_id, position, content)` déléguant à `argument_service` (FOR/AGAINST, rejet si hors ensemble)
    - `report_side_effect(user, problem_id, reform_id, content)` créant un `SideEffectReport` via `moderation_service` (validation longueur 1..5000)
    - _Requirements: 8.7, 8.8, 8.9, 10.1, 10.2, 10.3, 10.4, 13.3, 13.4, 13.5, 15.1_

  - [ ]* 4.9 Écrire le test de propriété — versionnement monotone et immuable
    - **Property 7 : Versionnement monotone et historique immuable**
    - **Validates: Requirements 8.7, 10.2, 10.3, 13.5**

  - [ ]* 4.10 Écrire le test de propriété — ordre croissant de l'historique des versions
    - **Property 8 : Ordre croissant de l'historique des versions**
    - **Validates: Requirements 10.4**

  - [ ]* 4.11 Écrire les tests unitaires de `legal_reform_service`
    - Délégation vote/version/position/signalement, mapping de modération vers `VISIBLE`/`PENDING`/`REJECTED`, refus longueur, erreurs 404 sur ressources absentes
    - _Requirements: 8.9, 10.5, 13.8, 15.2, 15.4, 15.5_

- [x] 5. Checkpoint — service domaine
  - Ensure all tests pass, ask the user if questions arise.

- [x] 6. Implémenter `legal_analysis_service` (agents IA au-dessus de RagPipeline)
  - [x] 6.1 Créer le service, l'énumération `AgentKind` et la lecture des analyses matérialisées
    - Créer `app/services/legal_analysis_service.py` avec `AgentKind` (9 valeurs) et `LegalAnalysisService(session, pipeline, audit)`
    - Lectures synchrones (jamais de génération en ligne) : `get_analysis`, `get_reform_analyses`, `get_simulation`, `get_legal_analysis` renvoyant `AnalysisResult | Unavailable`
    - _Requirements: 3.1, 5.1, 6.1, 7.1, 7.11, 12.6_

  - [x] 6.2 Implémenter la génération d'agent avec récupération obligatoire
    - `run_agent(reform_id, kind)` : appel `RagPipeline` avec persona + focus documentaire, température ∈ [0.0, 0.3] ; si retrieval vide/échec → persister `status=INDISPONIBLE` sans génération ni référence fabriquée
    - Validation des citations (chaque Affirmation_Juridique renvoie à une Source du contexte) ; abaisser la confiance des affirmations non sourcées
    - UPSERT dans `legal_analyses` (unicité `proposal_id, kind`) + journalisation `audit_service` (`RAG_ANALYSIS_PRODUCED`)
    - _Requirements: 3.3, 3.4, 3.5, 3.6, 3.7, 5.4, 6.4, 6.5, 7.10, 12.1, 12.2, 12.3, 12.4, 12.6_

  - [ ]* 6.3 Écrire le test de propriété — découplage IA (non-écriture décisions)
    - **Property 10 : Découplage IA — non-écriture dans les tables de décision**
    - **Validates: Requirements 11.1, 11.2, 11.3, 12.1**

  - [ ]* 6.4 Écrire le test de propriété — récupération obligatoire avant génération
    - **Property 11 : Récupération obligatoire avant génération**
    - **Validates: Requirements 3.4, 3.5, 3.6, 6.5, 7.10, 12.1**

  - [ ]* 6.5 Écrire le test de propriété — ancrage documentaire des affirmations
    - **Property 12 : Ancrage documentaire des affirmations juridiques**
    - **Validates: Requirements 3.3, 12.3, 12.4**

  - [ ]* 6.6 Écrire le test de propriété — température de génération bornée
    - **Property 13 : Température de génération bornée**
    - **Validates: Requirements 12.2**

  - [x] 6.7 Implémenter la Simulation et la Détection d'Effets Pervers
    - `run_agent(SIMULATION)` : effets par Catégorie_D_Acteur aux horizons 1/5/10 ans, marqueur d'hypothèse, sources ; payload `horizons`
    - `run_agent(EFFETS_PERVERS)` : `risks[]` avec ≥ 1 contre-mesure par risque et marqueur « risque identifié »
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4_

  - [ ]* 6.8 Écrire le test de propriété — complétude des horizons de la Simulation
    - **Property 14 : Complétude des horizons de la Simulation**
    - **Validates: Requirements 5.2, 5.3**

  - [ ]* 6.9 Écrire le test de propriété — présence des marqueurs de catégorie
    - **Property 15 : Présence des marqueurs de catégorie**
    - **Validates: Requirements 5.5, 6.3, 11.6, 16.5**

  - [ ]* 6.10 Écrire le test de propriété — chaque risque a au moins une contre-mesure
    - **Property 16 : Chaque risque possède au moins une contre-mesure**
    - **Validates: Requirements 6.2**

  - [x] 6.11 Implémenter l'agrégation de la Conclusion_Technique et l'équilibre Défenseur/Opposant
    - `run_reform_agents(reform_id)` (bundle 9 agents disponibles) et `_aggregate_conclusion(analyses)` non prescriptive (aucune reco d'adoption/rejet/vote, aucun classement), marqueurs et citations préservés
    - Équilibre : écart Défenseur/Opposant ≤ 1 (troncature au côté excédentaire) ; signaler l'absence si un rôle n'a aucun argument documenté, sans fabrication
    - _Requirements: 4.7, 7.8, 7.9, 7.10, 11.1, 11.2, 11.3, 11.4, 11.5_

  - [ ]* 6.12 Écrire le test de propriété — équilibre Défenseur/Opposant
    - **Property 17 : Équilibre DéfenseurIA / OpposantIA**
    - **Validates: Requirements 11.4, 11.5**

  - [ ]* 6.13 Écrire le test de propriété — Conclusion_Technique non prescriptive
    - **Property 18 : Conclusion_Technique non prescriptive**
    - **Validates: Requirements 4.7, 7.9, 11.1, 11.2, 11.3**

  - [x] 6.14 Implémenter l'assistant conversationnel `explain` et la comparaison de réformes
    - `explain(user, problem_id, reform_id, question)` via `RagPipeline` (≥ 1 Source citée ; si aucune Source → message d'absence sans altérer le Vote_Citoyen existant)
    - Question comparative (classification `COMPARATIVE`) : objectif, procédure, coût estimé, calendrier, effets documentés, contraintes juridiques, Sources, incertitudes ; info manquante → « indisponible pour ce critère » ; aucun classement de préférence
    - _Requirements: 4.5, 4.6, 4.7, 8.5, 8.6, 11.3_

  - [ ]* 6.15 Écrire les tests unitaires de `legal_analysis_service`
    - Retrieval vide → `INDISPONIBLE`, Conclusion à partir des seuls agents disponibles, journalisation par analyse, `explain` sans Source → message d'absence
    - _Requirements: 7.10, 8.6, 12.6_

- [x] 7. Checkpoint — service d'analyse IA
  - Ensure all tests pass, ask the user if questions arise.

- [x] 8. Routeur API REST `/api/v1/legal-problems`
  - [x] 8.1 Créer le routeur et les endpoints de lecture (publics)
    - Créer `app/api/v1/legal_problems.py` et le monter dans `app/api/v1/router.py`
    - `GET /legal-problems` (paginé, défaut 20/max 100/total), `GET /legal-problems/{id}` (404), `GET /{id}/reforms` (statu quo signalé), `GET /{id}/reforms/{reform_id}/votes`, `GET /{id}/reforms/{reform_id}/versions` (ordre croissant)
    - _Requirements: 1.4, 1.5, 1.6, 4.4, 9.4, 9.5, 10.4, 10.5, 14.1, 14.2, 14.3_

  - [x] 8.2 Ajouter les endpoints d'analyses IA (lecture matérialisée + 202)
    - `GET /{id}/analysis`, `GET /{id}/reforms/{reform_id}/simulation`, `GET /{id}/reforms/{reform_id}/analysis`
    - Renvoyer 200 si matérialisé, **202** si `PENDING` (déclenche/entérine la tâche Celery sans bloquer), 200 avec marqueur d'indisponibilité si `INDISPONIBLE`, 404 si ressource absente
    - _Requirements: 3.5, 5.6, 5.7, 6.5, 7.10, 7.11, 7.12_

  - [x] 8.3 Ajouter les endpoints d'écriture (authentifiés)
    - `POST /{id}/reforms/{reform_id}/votes` (UPSERT), `POST .../positions` (FOR/AGAINST), `POST .../amendments` (→ `ProposalVersion`), `POST .../side-effects` (→ modération), `POST .../explain`
    - Réutiliser le modèle d'erreur JSON normalisé et le rate limit existants ; renforcer le rate limit sur `explain`
    - 401 sans auth (état inchangé, y compris Vote existant), 422 corps invalide (champs énumérés), 404 ressource absente
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6, 8.7, 8.8, 8.10, 13.3, 13.4, 14.4, 14.5_

  - [ ]* 8.4 Écrire le test de propriété — pagination bornée et total exact
    - **Property 9 : Pagination bornée et total exact**
    - **Validates: Requirements 1.4, 14.1**
    - Cible : routeur `/legal-problems` (au plus `min(limit,100)` éléments, `total == N`)

  - [ ]* 8.5 Écrire les tests API d'exemple
    - 404 sur identifiants inexistants (1.6, 5.7, 7.12, 9.5, 10.5), 401 sans auth (8.10, 14.4), 422 corps invalides (`value∉{-1,0,1}`, `position∉{FOR,AGAINST}`, longueur>5000), 202 analyse en préparation
    - Fichier `tests/api/test_legal_problems_api.py`
    - _Requirements: 1.6, 5.7, 7.12, 8.10, 9.5, 10.5, 14.3, 14.4, 14.5_

- [x] 9. Checkpoint — API REST
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Tâches Celery de génération asynchrone des analyses
  - [x] 10.1 Implémenter la tâche `analyze_reform`
    - Créer `app/workers/legal_analysis.py` (tâche Celery enregistrée dans `celery_app.py`)
    - Déclencheurs : création d'une réforme, rattachement d'une Source, expiration du TTL de fraîcheur ; appelle `legal_analysis_service.run_reform_agents`, persiste `legal_analyses`, journalise l'audit
    - Génération hors du chemin HTTP synchrone ; retrieval obligatoire avant génération
    - _Requirements: 3.1, 5.1, 6.1, 7.1, 12.1, 12.6_

  - [ ]* 10.2 Écrire les tests unitaires de la tâche Celery
    - Enqueue/exécution avec `RagPipeline` mocké, écriture uniquement dans `legal_analyses` + audit, statut `INDISPONIBLE` si retrieval vide
    - _Requirements: 7.10, 12.1, 12.6_

- [x] 11. Pages SSR « Réparer la loi »
  - [x] 11.1 Implémenter les routes SSR liste et détail
    - Ajouter les routes `/reparer-la-loi` et `/reparer-la-loi/{slug}` dans `app/web/router.py`, consommant `legal_reform_service` et `legal_analysis_service`
    - Consultation sans authentification ; HTML complet rendu côté serveur (Jinja2/HTMX/Alpine/Tailwind)
    - _Requirements: 1.7, 16.1, 16.6_

  - [x] 11.2 Créer les templates liste et détail
    - Template liste : titre, résumé, nb de citoyens concernés, textes de loi, jurisprudence, Niveau_De_Complexité
    - Template détail : sections Analyse_Juridique (problème réel vs Simplification_Médiatique, citations numérotées), Réformes comparées côte à côte (statu quo signalé), Simulation (grille acteur × 1/5/10 ans), Effets Pervers (risque + contre-mesure + marqueur), Agents_IA + Conclusion_Technique, décomptes de votes (pourcentage **toujours** avec nombre absolu), Sources_Juridiques avec liens Légifrance/jurisprudence
    - Marqueurs visibles estimation/hypothèse et catégorie (fait/estimation/opinion/hypothèse/désaccord) ; libellé « aucun contenu » par section vide ; message « contenu indisponible » sans détail technique ; **aucun** accès aux fonctionnalités hors-périmètre (Exigence 17)
    - _Requirements: 1.3, 3.2, 3.7, 4.4, 5.5, 9.3, 11.6, 12.5, 16.2, 16.3, 16.5, 16.7, 17.1, 17.2, 17.3, 17.5_

  - [x] 11.3 Implémenter les fragments HTMX d'actions par réforme
    - Approuver, Rejeter, Proposer un amendement, Demander une explication, Signaler un effet secondaire
    - Utilisateur non authentifié tentant une action → invitation à s'authentifier, sans altération d'état
    - _Requirements: 8.10, 16.4_

  - [ ]* 11.4 Écrire les tests SSR
    - Présence des sections/marqueurs, libellé « section vide », 404 sans détail, absence des fonctionnalités hors-périmètre, invitation à l'authentification sur action non authentifiée
    - Fichier `tests/api/test_reparer_la_loi_web.py`
    - _Requirements: 16.2, 16.3, 16.5, 16.7, 17.1, 17.4_

- [x] 12. Script de seed des Problèmes Juridiques
  - [x] 12.1 Implémenter le seed ≥ 10 Problèmes Juridiques
    - Script de seed (aligné sur les scripts existants) amorçant **≥ 10** Problèmes_Juridiques, chacun avec 3–5 Réformes_Proposées (`Proposal`) dont exactement un statu quo, rattachées via `legal_problem_reforms`
    - Rattacher un `theme_id` valide et des Sources `LEGISLATION` ; respecter la cardinalité validée par le service
    - _Requirements: 1.2, 2.4, 4.1, 4.3, 13.6_

  - [ ]* 12.2 Écrire le test unitaire du seed
    - Vérifier ≥ 10 problèmes, cardinalité 3–5 dont 1 statu quo par problème, slugs uniques
    - _Requirements: 1.2, 2.6, 4.1_

- [x] 13. Intégration finale et câblage
  - [x] 13.1 Câbler la gestion d'erreurs et la journalisation transversales
    - Brancher `ReformCardinalityError` → 422, les 404/401/422 sur le modèle d'erreur JSON normalisé (`code`, `message`, `request_id`, `fields[]`) et la corrélation `X-Request-ID`
    - Vérifier la journalisation d'audit des analyses (`RAG_ANALYSIS_PRODUCED`) et des décisions de modération
    - _Requirements: 4.2, 12.6, 13.7, 14.3, 14.4, 14.5, 15.3_

  - [ ]* 13.2 Écrire les tests d'intégration bout-en-bout (automatisés)
    - Parcours : lister problèmes → détail → réformes → analyse matérialisée (202 puis 200 après tâche) → vote → décomptes → amendement → historique
    - Modération d'un signalement (mapping `VISIBLE`/`PENDING`/`REJECTED`) et échec de rattachement de Source (état inchangé)
    - Fichier `tests/api/test_legal_problems_integration.py`
    - _Requirements: 8.4, 9.2, 10.2, 13.7, 15.2, 15.4, 15.5, 15.6_

- [x] 14. Checkpoint final — Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise. Lancer `make lint`, `make type`, `make test`.

## Notes

- Les sous-tâches marquées `*` sont optionnelles (tests) et peuvent être différées pour un MVP plus rapide, mais toutes les propriétés de la conception y sont couvertes.
- Chaque tâche référence des exigences précises (traçabilité) et s'appuie sur la précédente ; le câblage final évite tout code orphelin.
- Les analyses IA ne sont **jamais** générées dans le chemin HTTP synchrone : lecture matérialisée + 202 côté API, génération côté Celery.
- Les propriétés PBT sont implémentées avec Hypothesis (≥ 100 itérations) et Providers IA simulés pour isoler la logique du coût des appels externes.
- Aucun modèle/service redondant : une Réforme_Proposée **est** une `Proposal` ; vote/version/argument/source/thème/popularité/modération/audit passent par les services existants.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "3.1", "3.3"] },
    { "id": 3, "tasks": ["3.2", "3.4", "4.1", "6.1"] },
    { "id": 4, "tasks": ["4.2", "4.3", "4.5", "4.8", "6.2", "6.7", "6.14"] },
    { "id": 5, "tasks": ["4.4", "4.6", "4.7", "4.9", "4.10", "4.11", "6.3", "6.4", "6.5", "6.6", "6.8", "6.9", "6.10", "6.11", "6.15", "10.1"] },
    { "id": 6, "tasks": ["6.12", "6.13", "8.1", "8.2", "8.3", "10.2"] },
    { "id": 7, "tasks": ["8.4", "8.5", "11.1"] },
    { "id": 8, "tasks": ["11.2", "11.3", "12.1"] },
    { "id": 9, "tasks": ["11.4", "12.2", "13.1"] },
    { "id": 10, "tasks": ["13.2"] }
  ]
}
```
