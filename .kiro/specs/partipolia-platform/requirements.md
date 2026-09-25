# Requirements Document

_Document d'Exigences — PARTIPOLAI (V1)_

## Introduction

PARTIPOLAI est une plateforme de parti politique virtuel pour la France 2027, dont la
**Proposition** de politique publique constitue l'entité centrale. La Plateforme permet de créer
des Propositions, de les classer par Thèmes, de les documenter avec des Sources vérifiables, de
voter, de débattre par Commentaires et Arguments, de proposer des amendements (nouvelles
versions), de construire progressivement un Programme, d'interroger une IA politique spécialisée,
de mener une recherche documentaire augmentée (RAG) affichant ses Sources, et de conserver
l'historique complet des modifications.

Le présent document couvre le **périmètre V1 complet** défini par le cahier des charges technique
PARTIPOLAI : gestion des comptes, référentiel thématique, cycle de vie des Propositions et de
leurs versions, vote, débat argumenté, Sources et documents, pipeline d'ingestion documentaire,
recherche hybride et assistant IA/RAG, construction de Programme, équipes, suivi de mandat
(engagements et indicateurs), modération, statistiques anonymisées, API REST, sécurité,
conformité RGPD, observabilité et infrastructure.

**Principe directeur (section 73) :** la Plateforme restitue des **résultats de participation** et
l'IA **documente et assiste** — elle **ne vote pas** et **ne décide pas du Programme**. L'inclusion
définitive d'une Proposition dans un Programme demeure un acte **explicite et traçable**, jamais
produit automatiquement par l'IA.

### Périmètre V1 (inclus)

- Assistant conversationnel documentaire IA/RAG : classification de question, réécriture de
  requête, recherche hybride (lexicale plein-texte + sémantique pgvector), fusion de scores,
  reranking, construction de contexte, génération à faible température, validation des citations,
  garde-fous anti-hallucination, citations numérotées affichées comme Sources.
- Pipeline d'ingestion documentaire (URL → téléchargement → détection de format → extraction →
  nettoyage → déduplication par checksum → chunking → embeddings → PostgreSQL/pgvector), formats
  HTML, PDF, TXT, CSV.
- Construction de Programme, équipes (teams / team_members), suivi de mandat (engagements et
  indicateurs), journal d'audit, statistiques anonymisées.

### Hors périmètre V1 (différé)

- Vérification d'identité forte via FranceConnect.
- Modération pilotée par IA à trois niveaux avec tri prioritaire.
- Détection anti-manipulation avancée par scoring comportemental.

> La modération V1 est **simple** (filtre automatique → classification → publication si conforme /
> mise en File_De_Modération si douteux, avec décisions auditables), la limitation de débit V1 est
> **basique** sur les points d'accès sensibles, et la prévention des doublons repose sur la
> **détection par embeddings à la création d'une Proposition**.

### Décisions de périmètre validées

- **Modèle de vote V1** : vote à trois valeurs — *Pour* (+1) / *Neutre* (0) / *Contre* (-1), avec
  au plus un Vote par Utilisateur et par Proposition.
- **Historique** : une Proposition publiée ne doit jamais être écrasée sans conservation de
  l'historique de ses versions.
- **Priorité** : la priorité est un attribut du Programme (program_proposals), non de la
  Proposition.
- **Architecture V1 (section 72)** : monolithe modulaire (FastAPI API + Web + Services,
  PostgreSQL + pgvector, Redis, worker Celery, RAG intégré au backend et au worker). Pas de
  micro-services au démarrage.
- **Langue** : interface et API en français pour la V1.

## Glossary

- **Plateforme** : l'ensemble applicatif PARTIPOLAI (frontend, API, services d'arrière-plan,
  worker asynchrone).
- **API** : l'interface REST versionnée exposée par la Plateforme sous le préfixe `/api/v1`.
- **Interface_Publique** : les pages accessibles sans authentification.
- **Utilisateur** : personne disposant d'un compte (champs : id, email unique, password_hash,
  display_name, is_active, is_verified, is_admin, horodatages).
- **Utilisateur_Authentifié** : Utilisateur dont la session JWT est active.
- **Contributeur** : Utilisateur_Authentifié ayant créé au moins une Proposition.
- **Administrateur** : Utilisateur dont l'attribut is_admin est vrai.
- **Thème** : catégorie de politique publique du Référentiel_Thématique (25 Thèmes définis).
- **Référentiel_Thématique** : l'ensemble structuré des Thèmes.
- **Proposition** : entité centrale décrivant une mesure de politique publique (attributs :
  theme_id, author_id, slug, title, problem, description, expected_impact, implementation_delay,
  estimated_cost, estimated_savings, estimated_revenue, funding_description, legal_constraints,
  status, version).
- **Statut_De_Proposition** : valeur parmi DRAFT, PENDING_REVIEW, PUBLISHED, ARCHIVED, REJECTED.
- **Version_De_Proposition** : entrée d'historique complète d'une Proposition (proposal_versions,
  avec change_summary).
- **Vote** : expression d'une préférence (value parmi +1, 0, -1), unique par (proposition,
  Utilisateur).
- **Argument** : élément textuel de position FOR ou AGAINST rattaché à une Proposition.
- **Commentaire** : contribution textuelle rattachée à une Proposition (parent_id pour les fils,
  status VISIBLE).
- **Source** : référence documentaire (title, url, publisher, source_type parmi OFFICIAL,
  ACADEMIC, STATISTICAL, MEDIA, REPORT, LEGISLATION, OTHER, publication_date, is_verified).
- **Document** : ressource ingérée rattachée à une Source (source_id, checksum unique, metadata
  jsonb).
- **Chunk_De_Document** : segment d'un Document (chunk_index, content, token_count, embedding
  VECTOR de dimension configurable, metadata).
- **Programme** : ensemble de Propositions retenues (attribut status).
- **Programme_Proposition** : association d'une Proposition à un Programme (priority, included_at).
- **Équipe** : entité teams regroupant des membres (team_members : role, bio).
- **Engagement** : entité commitments (status parmi NOT_STARTED, IN_PROGRESS, COMPLETED, MODIFIED,
  ABANDONED, target_date, progress, notes).
- **Indicateur** : entité indicators (name, unit, baseline, target, current_value, source_id).
- **Journal_D_Audit** : entité audit_logs (action, entity_type, entity_id, old_data, new_data,
  ip_hash — l'adresse IP brute n'est jamais conservée).
- **Assistant_IA** : le service conversationnel documentaire fondé sur le pipeline RAG.
- **Pipeline_RAG** : la chaîne classification → réécriture → recherche hybride → fusion →
  reranking → construction de contexte → génération → validation des citations → réponse.
- **Recherche_Hybride** : combinaison de la recherche lexicale plein-texte PostgreSQL
  (tsvector/tsquery) et de la recherche sémantique pgvector (distance cosinus).
- **Pipeline_D_Ingestion** : la chaîne URL → téléchargement → détection de format → extraction →
  nettoyage → déduplication → chunking → embeddings → PostgreSQL/pgvector.
- **LLMProvider** : abstraction fournisseur de génération (Protocol `generate(messages,
  temperature)`).
- **EmbeddingProvider** : abstraction fournisseur d'embeddings (Protocol `embed(texts)`).
- **Worker_Celery** : le service exécutant les tâches asynchrones.
- **Moteur_De_Modération** : composant appliquant le filtre automatique et la classification
  simple des Commentaires.
- **File_De_Modération** : liste des contenus douteux en attente de décision humaine.
- **Taux_De_Soutien** : support_count / (support_count + oppose_count) pour une Proposition.
- **RGPD** : Règlement Général sur la Protection des Données.
- **JWT** : jeton d'authentification (access token + refresh token).

## Requirements

### Exigence 1 — Comptes utilisateurs et authentification

**User Story:** En tant que citoyen, je veux créer un compte et m'authentifier, afin de contribuer,
voter et débattre de manière fiable.

#### Critères d'acceptation

1. WHEN un visiteur soumet une inscription avec une adresse email non déjà enregistrée, THE
   Plateforme SHALL créer un Utilisateur avec email unique, password_hash, display_name, is_active
   vrai, is_verified faux, is_admin faux et horodatages de création.
2. IF un visiteur soumet une inscription avec une adresse email déjà enregistrée, THEN THE
   Plateforme SHALL refuser la création et retourner un message d'erreur générique n'indiquant pas
   si l'email est déjà enregistré.
3. THE Plateforme SHALL stocker le mot de passe uniquement sous forme hachée dans password_hash.
4. WHEN un Utilisateur soumet des identifiants valides sur `POST /api/v1/auth/login`, THE
   Plateforme SHALL émettre un JWT access token et un JWT refresh token.
5. WHERE le client est l'application web, THE Plateforme SHALL transmettre les jetons dans des
   cookies HttpOnly, Secure et SameSite=Lax.
6. WHEN un Utilisateur_Authentifié appelle `POST /api/v1/auth/refresh` avec un refresh token
   valide, THE Plateforme SHALL émettre un nouveau access token.
7. WHEN un Utilisateur_Authentifié appelle `POST /api/v1/auth/logout`, THE Plateforme SHALL
   invalider la session courante.
8. WHEN un Utilisateur_Authentifié appelle `GET /api/v1/auth/me`, THE Plateforme SHALL retourner
   les données du compte courant à l'exclusion de password_hash.
9. IF un Utilisateur soumet des identifiants invalides, THEN THE Plateforme SHALL refuser
   l'authentification et retourner un message d'erreur générique.
10. THE Plateforme SHALL réserver la création de Propositions, le vote, la publication de
    Commentaires et d'Arguments aux Utilisateurs_Authentifiés.

### Exigence 2 — Référentiel thématique

**User Story:** En tant que Contributeur, je veux classer les Propositions selon un référentiel de
Thèmes, afin de structurer et retrouver les mesures par domaine de politique publique.

#### Critères d'acceptation

1. THE Plateforme SHALL fournir un Référentiel_Thématique comportant exactement les 25 Thèmes
   définis pour la V1.
2. THE Plateforme SHALL exposer la liste des Thèmes via `GET /api/v1/themes`.
3. THE Plateforme SHALL exposer le détail d'un Thème via `GET /api/v1/themes/{id}`.
4. THE Plateforme SHALL exposer les Propositions rattachées à un Thème via
   `GET /api/v1/themes/{id}/proposals`.
5. THE Plateforme SHALL rattacher chaque Proposition à exactement un Thème.

> Thèmes V1 (slugs) : economie ; finances-publiques ; fiscalite ; travail ; sante ; education ;
> recherche ; ia-numerique ; industrie ; energie ; ecologie ; agriculture ; logement ; securite ;
> justice ; immigration ; defense ; europe ; affaires-etrangeres ; institutions ;
> services-publics ; transports ; outre-mer ; famille ; culture-sport.

### Exigence 3 — Objet Proposition (mesure)

**User Story:** En tant que Contributeur, je veux décrire une mesure de manière structurée, afin
qu'elle soit comparable, débattable et traçable.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter chaque Proposition avec les attributs : theme_id, author_id,
   slug, title, problem, description, expected_impact, implementation_delay, estimated_cost,
   estimated_savings, estimated_revenue, funding_description, legal_constraints, status et version.
2. WHEN une Proposition est créée, THE Plateforme SHALL lui attribuer un identifiant unique, un
   slug, le statut initial DRAFT et la version 1.
3. THE Plateforme SHALL restreindre le Statut_De_Proposition aux valeurs DRAFT, PENDING_REVIEW,
   PUBLISHED, ARCHIVED et REJECTED.
4. WHEN une Proposition est modifiée, THE Plateforme SHALL enregistrer une Version_De_Proposition
   complète comportant un change_summary et incrémenter la version.
5. THE Plateforme SHALL conserver l'intégralité des Versions_De_Proposition d'une Proposition
   publiée sans jamais écraser une version antérieure.

### Exigence 4 — Création et gestion des Propositions (API)

**User Story:** En tant que Contributeur, je veux créer, consulter, filtrer et modifier des
Propositions, afin d'alimenter et faire évoluer le catalogue des mesures.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié appelle `POST /api/v1/proposals` avec un corps valide, THE
   Plateforme SHALL créer la Proposition à l'état DRAFT.
2. IF un Utilisateur_Authentifié soumet une création de Proposition avec un champ obligatoire
   manquant ou invalide, THEN THE Plateforme SHALL refuser l'enregistrement et retourner les
   champs en erreur.
3. THE API SHALL exposer la liste des Propositions via `GET /api/v1/proposals` avec les filtres et
   paramètres theme, status, sort, search, page et limit.
4. THE API SHALL exposer le détail d'une Proposition via `GET /api/v1/proposals/{id}`.
5. WHEN l'auteur d'une Proposition appelle `PUT /api/v1/proposals/{id}` avec un corps valide, THE
   Plateforme SHALL enregistrer une nouvelle Version_De_Proposition conformément à l'Exigence 3.
6. WHEN l'auteur d'une Proposition appelle `DELETE /api/v1/proposals/{id}`, THE Plateforme SHALL
   faire passer la Proposition au statut ARCHIVED en conservant son historique.
7. IF un Utilisateur non-auteur et non-Administrateur tente de modifier ou de supprimer une
   Proposition, THEN THE Plateforme SHALL refuser l'action.

### Exigence 5 — Détection de doublons à la création

**User Story:** En tant que Contributeur, je veux être alerté des Propositions similaires
existantes, afin d'éviter les doublons et d'améliorer les mesures existantes.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié crée une Proposition, THE Plateforme SHALL calculer un
   embedding de la Proposition et rechercher les Propositions proches par similarité.
2. WHERE au moins une Proposition proche est trouvée, THE Plateforme SHALL présenter le message
   « Des propositions similaires existent » avec les actions Consulter, Créer quand même et
   Améliorer une proposition existante.
3. IF l'Utilisateur choisit Créer quand même, THEN THE Plateforme SHALL créer la Proposition sans
   blocage.

### Exigence 6 — Système de vote

**User Story:** En tant qu'Utilisateur_Authentifié, je veux exprimer mon soutien, mon opposition ou
ma neutralité sur une Proposition, afin de participer à la mesure du soutien citoyen.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié appelle `POST /api/v1/proposals/{id}/votes` avec un corps
   `{value}`, THE Plateforme SHALL enregistrer une valeur unique parmi +1, 0 et -1.
2. THE Plateforme SHALL garantir l'unicité d'un Vote par (proposition_id, user_id).
3. WHEN un Utilisateur_Authentifié vote de nouveau sur une Proposition déjà votée, THE Plateforme
   SHALL remplacer la valeur du Vote précédent sans créer de Vote supplémentaire.
4. WHEN un Utilisateur_Authentifié appelle `DELETE /api/v1/proposals/{id}/votes`, THE Plateforme
   SHALL supprimer son Vote et l'exclure des décomptes.
5. THE API SHALL exposer les décomptes de Votes d'une Proposition via
   `GET /api/v1/proposals/{id}/votes`.
6. IF un Utilisateur non authentifié tente de voter, THEN THE Plateforme SHALL refuser le Vote et
   inviter à l'authentification.

### Exigence 7 — Algorithme de popularité

**User Story:** En tant que visiteur, je veux voir le soutien d'une Proposition à travers des
décomptes explicites, afin d'éviter qu'un simple tri par nombre de votes n'oriente ma lecture.

#### Critères d'acceptation

1. THE Plateforme SHALL calculer, pour chaque Proposition, support_count, oppose_count et
   participation_count.
2. THE Plateforme SHALL calculer le Taux_De_Soutien comme support_count / (support_count +
   oppose_count) et SHALL renvoyer 0 lorsque ce dénominateur est nul.
3. THE Plateforme SHALL s'abstenir de classer les Propositions par un simple `ORDER BY
   vote_count`.
4. WHERE un pourcentage est affiché, THE Interface_Publique SHALL afficher également le nombre
   absolu de Votes correspondant.

### Exigence 8 — Arguments

**User Story:** En tant qu'Utilisateur_Authentifié, je veux ajouter des arguments pour ou contre une
Proposition, afin d'étayer le débat.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié crée un Argument sur une Proposition, THE Plateforme SHALL
   enregistrer l'Argument avec une position parmi FOR et AGAINST.
2. THE API SHALL exposer la création, la lecture, la modification et la suppression des Arguments.
3. IF un Utilisateur non-auteur et non-Administrateur tente de modifier ou supprimer un Argument,
   THEN THE Plateforme SHALL refuser l'action.

### Exigence 9 — Commentaires et débat

**User Story:** En tant qu'Utilisateur_Authentifié, je veux commenter une Proposition et répondre à
d'autres commentaires, afin de contribuer au débat en fils de discussion.

#### Critères d'acceptation

1. WHEN un Utilisateur_Authentifié publie un Commentaire sur une Proposition, THE Plateforme SHALL
   soumettre le Commentaire au Moteur_De_Modération conformément à l'Exigence 17.
2. THE Plateforme SHALL permettre à un Commentaire de référencer un Commentaire parent (parent_id)
   afin de former des fils de discussion.
3. THE API SHALL exposer la création, la lecture, la modification et la suppression des
   Commentaires.
4. WHERE un Commentaire est publié, THE Plateforme SHALL l'enregistrer avec le statut VISIBLE.
5. IF un Utilisateur non authentifié tente de publier un Commentaire, THEN THE Plateforme SHALL
   refuser la publication et inviter à l'authentification.

### Exigence 10 — Sources documentaires

**User Story:** En tant que Contributeur, je veux rattacher des Sources vérifiables à une
Proposition, afin de documenter la mesure.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter chaque Source avec title, url, publisher, source_type,
   publication_date et is_verified.
2. THE Plateforme SHALL restreindre source_type aux valeurs OFFICIAL, ACADEMIC, STATISTICAL,
   MEDIA, REPORT, LEGISLATION et OTHER.
3. WHEN une Source est rattachée à une Proposition, THE Plateforme SHALL enregistrer l'association
   proposal_sources avec un relevance_score.
4. THE Plateforme SHALL permettre à un Administrateur de gérer les Sources.

### Exigence 11 — Pipeline d'ingestion documentaire

**User Story:** En tant qu'Administrateur, je veux ingérer des documents depuis leurs URL, afin
d'alimenter la base documentaire utilisée par l'Assistant_IA.

#### Critères d'acceptation

1. WHEN un Administrateur déclenche l'ingestion d'un Document, THE Pipeline_D_Ingestion SHALL
   exécuter successivement le téléchargement, la détection de format, l'extraction de texte, le
   nettoyage, la déduplication, le chunking, la génération des embeddings et l'écriture dans
   PostgreSQL/pgvector.
2. THE Pipeline_D_Ingestion SHALL prendre en charge les formats HTML, PDF, TXT et CSV en V1.
3. THE Plateforme SHALL calculer un checksum unique par Document et SHALL éviter de réindexer un
   Document dont le checksum est déjà enregistré.
4. THE Pipeline_D_Ingestion SHALL découper chaque Document en Chunks_De_Document avec une taille
   de 800 tokens et un chevauchement de 120 tokens.
5. THE Plateforme SHALL conserver, pour chaque Chunk_De_Document, les métadonnées source_id,
   document_id, page, section et publication_date.
6. THE Plateforme SHALL stocker les embeddings des Chunks_De_Document dans une colonne VECTOR
   indexée par un index HNSW pgvector.

### Exigence 12 — Recherche hybride

**User Story:** En tant qu'Assistant_IA, je veux retrouver les passages documentaires les plus
pertinents, afin de fonder mes réponses sur des Sources.

#### Critères d'acceptation

1. THE Recherche_Hybride SHALL combiner une recherche lexicale plein-texte PostgreSQL
   (tsvector/tsquery) et une recherche sémantique pgvector par distance cosinus.
2. THE Recherche_Hybride SHALL fusionner les résultats selon un score configurable égal à
   0,40 × sémantique + 0,30 × lexical + 0,20 × qualité_de_source + 0,10 × récence.
3. WHEN la fusion produit une liste de résultats, THE Recherche_Hybride SHALL appliquer un
   reranking réduisant les 20 meilleurs résultats aux 6 meilleurs.

### Exigence 13 — Assistant IA documentaire (RAG)

**User Story:** En tant qu'Utilisateur, je veux interroger une IA politique spécialisée sur les
Sources, afin d'obtenir des réponses documentées et citées.

#### Critères d'acceptation

1. WHEN un Utilisateur appelle `POST /api/v1/chat` avec `{message, proposal_id}`, THE
   Assistant_IA SHALL retourner `{answer, sources[], confidence}`.
2. THE Pipeline_RAG SHALL classer chaque question parmi générale, sur-proposition, documentaire et
   comparative.
3. THE Pipeline_RAG SHALL réécrire la requête, exécuter la Recherche_Hybride, construire un
   contexte à partir des passages retenus, générer la réponse via le LLMProvider à faible
   température, valider les citations, puis retourner la réponse.
4. THE Assistant_IA SHALL produire, pour chaque réponse, des citations numérotées affichées comme
   Sources.
5. THE Assistant_IA SHALL répondre uniquement à partir du contexte documentaire fourni.
6. THE Assistant_IA SHALL distinguer les faits, les estimations, les opinions, les hypothèses et
   les désaccords entre Sources.
7. THE Assistant_IA SHALL associer chaque affirmation factuelle importante à une Source.
8. IF l'information nécessaire est absente du contexte documentaire, THEN THE Assistant_IA SHALL
   indiquer explicitement que l'information est indisponible plutôt que d'inventer une réponse.
9. THE Assistant_IA SHALL s'abstenir d'inventer des nombres.
10. THE Assistant_IA SHALL s'abstenir de transformer une information descriptive en recommandation
    politique.

### Exigence 14 — Garde-fous anti-hallucination

**User Story:** En tant qu'exploitant de la Plateforme, je veux prévenir les hallucinations de
l'Assistant_IA, afin de préserver la fiabilité des réponses.

#### Critères d'acceptation

1. THE Assistant_IA SHALL exiger une étape de récupération documentaire (retrieval) avant toute
   génération.
2. THE Assistant_IA SHALL générer les réponses à faible température.
3. THE Assistant_IA SHALL exiger des citations pour les affirmations factuelles.
4. THE Assistant_IA SHALL vérifier les citations produites.
5. WHEN une affirmation factuelle n'est associée à aucune Source, THE Assistant_IA SHALL détecter
   cette affirmation non sourcée.
6. WHERE l'information est insuffisante, THE Assistant_IA SHALL pouvoir répondre « information
   insuffisante ».
7. THE Plateforme SHALL journaliser chaque réponse du Pipeline_RAG.

### Exigence 15 — Neutralité de l'Assistant sur les questions politiques sensibles

**User Story:** En tant qu'exploitant de la Plateforme, je veux que l'Assistant_IA reste un outil de
documentation, afin qu'il ne devienne pas un instrument de persuasion individuelle.

#### Critères d'acceptation

1. WHERE une question porte sur un choix politique sensible, THE Assistant_IA SHALL privilégier la
   formulation « Voici les arguments documentés » plutôt que « Vous devriez voter pour ».
2. WHEN un Utilisateur demande de comparer une mesure A et une mesure B, THE Assistant_IA SHALL
   comparer sur des critères factuels : coût, calendrier, effets documentés, contraintes
   juridiques, Sources et incertitudes.
3. THE Assistant_IA SHALL s'abstenir de formuler une recommandation de vote individuelle.

### Exigence 16 — Abstraction des fournisseurs IA

**User Story:** En tant qu'exploitant de la Plateforme, je veux abstraire les fournisseurs de LLM et
d'embeddings, afin de ne pas coupler l'IA au domaine et de rester configurable.

#### Critères d'acceptation

1. THE Plateforme SHALL exposer un LLMProvider fournissant `generate(messages, temperature)`.
2. THE Plateforme SHALL exposer un EmbeddingProvider fournissant `embed(texts)`.
3. THE Plateforme SHALL rendre le fournisseur et le modèle configurables pour la génération et
   pour les embeddings.
4. THE Plateforme SHALL isoler les fournisseurs IA de la couche domaine.

### Exigence 17 — Modération simple des contenus

**User Story:** En tant qu'exploitant de la Plateforme, je veux modérer les Commentaires selon un
processus simple et auditable, afin de retirer les contenus indésirables tout en préservant les
opinions.

#### Critères d'acceptation

1. WHEN un Commentaire est soumis, THE Moteur_De_Modération SHALL lui appliquer un filtre
   automatique puis une classification.
2. WHEN la classification conclut « conforme », THE Moteur_De_Modération SHALL publier le
   Commentaire avec le statut VISIBLE.
3. WHEN la classification conclut « douteux », THE Moteur_De_Modération SHALL placer le
   Commentaire dans la File_De_Modération sans le publier.
4. THE Plateforme SHALL consigner chaque décision de modération dans le Journal_D_Audit afin de la
   rendre auditable.

### Exigence 18 — Construction de Programme

**User Story:** En tant qu'Administrateur, je veux construire un Programme à partir de Propositions,
afin de restituer un ensemble cohérent de mesures.

#### Critères d'acceptation

1. THE API SHALL exposer le Programme via `GET /api/v1/program` et ses Propositions via
   `GET /api/v1/program/proposals`.
2. WHEN un Administrateur ajoute une Proposition au Programme, THE Plateforme SHALL créer une
   association Programme_Proposition avec priority et included_at.
3. WHEN un Administrateur retire une Proposition du Programme, THE Plateforme SHALL supprimer
   l'association Programme_Proposition correspondante.
4. THE Plateforme SHALL traiter la priorité comme un attribut de l'association Programme_Proposition
   et non de la Proposition.
5. THE API SHALL exposer les statistiques du Programme via `GET /api/v1/program/statistics`.

### Exigence 19 — Vue « Programme en construction »

**User Story:** En tant qu'Administrateur, je veux visualiser une proposition de Programme calculée,
afin de repérer des mesures candidates, sans que ce calcul ne décide de l'inclusion.

#### Critères d'acceptation

1. THE Plateforme SHALL pouvoir calculer une vue « Programme en construction » regroupant les
   Propositions fortement soutenues, suffisamment documentées, non contradictoires et estimées
   financièrement.
2. THE Plateforme SHALL présenter la vue « Programme en construction » comme un résultat de
   participation, sans la traiter comme un Programme définitif.
3. THE Plateforme SHALL exiger que l'inclusion définitive d'une Proposition dans un Programme soit
   explicite et traçable conformément à l'Exigence 18.

### Exigence 20 — Équipes

**User Story:** En tant qu'Administrateur, je veux constituer des équipes et leurs membres, afin de
présenter les personnes portant le Programme.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter les Équipes (teams) et leurs membres (team_members).
2. THE Plateforme SHALL représenter chaque membre d'Équipe avec un role et une bio.
3. WHEN un Administrateur crée, modifie ou supprime une Équipe ou un membre, THE Plateforme SHALL
   enregistrer la modification.

### Exigence 21 — Suivi de mandat (engagements et indicateurs)

**User Story:** En tant que visiteur, je veux suivre les engagements et leurs indicateurs, afin
d'apprécier l'avancement du mandat.

#### Critères d'acceptation

1. THE Plateforme SHALL représenter chaque Engagement avec status, target_date, progress et notes.
2. THE Plateforme SHALL restreindre le status d'un Engagement aux valeurs NOT_STARTED,
   IN_PROGRESS, COMPLETED, MODIFIED et ABANDONED.
3. THE Plateforme SHALL représenter chaque Indicateur avec name, unit, baseline, target,
   current_value et source_id.
4. WHEN la valeur d'un Indicateur ou le statut d'un Engagement est mis à jour, THE Plateforme SHALL
   enregistrer la modification.

### Exigence 22 — Statistiques anonymisées

**User Story:** En tant que visiteur, je veux consulter des statistiques globales anonymisées, afin
d'apprécier l'ampleur de la participation.

#### Critères d'acceptation

1. THE API SHALL exposer, via `GET /api/v1/statistics`, les compteurs d'Utilisateurs, de
   Propositions, de Votes, de Commentaires, de Sources et de Thèmes.
2. THE Plateforme SHALL présenter les statistiques sous forme agrégée et anonymisée.
3. THE Plateforme SHALL s'abstenir de produire des statistiques individuelles permettant de
   reconstituer les opinions politiques d'un Utilisateur.

### Exigence 23 — Tâches asynchrones (Celery)

**User Story:** En tant qu'exploitant de la Plateforme, je veux exécuter les opérations longues en
arrière-plan, afin de ne pas bloquer l'API.

#### Critères d'acceptation

1. THE Worker_Celery SHALL fournir les tâches ingest_document, extract_document, chunk_document,
   generate_embeddings, reindex_document, moderate_comment et recalculate_statistics.
2. WHEN une opération longue est déclenchée, THE Plateforme SHALL l'exécuter via le Worker_Celery.
3. THE Plateforme SHALL s'abstenir de bloquer l'API FastAPI pendant l'exécution d'une opération
   longue.

### Exigence 24 — Pages de restitution

**User Story:** En tant que visiteur, je veux consulter la fiche d'une mesure et la page du
Programme, afin de comprendre le contenu et l'état de la participation.

#### Critères d'acceptation

1. THE Interface_Publique SHALL afficher, pour une Proposition, le titre, le problème, la
   proposition, le pourquoi, les décomptes de Votes pour et contre, le coût, le financement, les
   Arguments pour et contre, les Sources, le débat/commentaires, et l'action IA « Interroger les
   sources sur cette mesure ».
2. THE Interface_Publique SHALL afficher, sur la page du Programme, la liste des Thèmes et, pour
   chaque mesure, le nombre de propositions, le nombre de votes, le soutien agrégé et les coûts,
   recettes et économies estimés.
3. WHERE des agrégats financiers sont affichés sur la page du Programme, THE Interface_Publique
   SHALL indiquer clairement les hypothèses utilisées.
4. THE Interface_Publique SHALL afficher ces pages sans exiger d'authentification pour la
   consultation.

### Exigence 25 — Frontend V1

**User Story:** En tant qu'exploitant de la Plateforme, je veux un frontend rendu côté serveur,
afin d'éviter une SPA au lancement tout en préparant des clients ultérieurs.

#### Critères d'acceptation

1. THE Plateforme SHALL rendre les pages côté serveur avec Jinja2, HTMX, Alpine.js et Tailwind.
2. THE Plateforme SHALL exposer une API REST complète permettant des clients ultérieurs (React,
   mobile, natif, tiers).

### Exigence 26 — Sécurité et middleware

**User Story:** En tant qu'exploitant de la Plateforme, je veux protéger les échanges et les points
d'accès, afin de garantir la sécurité et la disponibilité.

#### Critères d'acceptation

1. THE Plateforme SHALL servir l'ensemble du trafic via HTTPS.
2. THE Plateforme SHALL appliquer une politique CORS.
3. THE Plateforme SHALL attribuer un identifiant de requête (X-Request-ID) à chaque requête et
   produire une journalisation structurée.
4. WHEN le volume de requêtes d'un client sur un point d'accès sensible (login, register, chat,
   proposals, comments, votes) dépasse un seuil défini, THE Plateforme SHALL appliquer une
   limitation de débit basique.
5. THE Plateforme SHALL protéger particulièrement le point d'accès `POST /api/v1/chat` contre les
   abus.
6. THE Plateforme SHALL ajouter les en-têtes de sécurité HTTP X-Request-ID, X-Content-Type-Options,
   X-Frame-Options, Referrer-Policy et Content-Security-Policy.
7. THE Plateforme SHALL gérer les exceptions de manière globale.
8. THE Plateforme SHALL authentifier les requêtes via JWT (access token + refresh token).

### Exigence 27 — Health checks

**User Story:** En tant qu'exploitant de la Plateforme, je veux des points de contrôle de santé,
afin de surveiller la disponibilité des services.

#### Critères d'acceptation

1. WHEN un client appelle `GET /health`, THE Plateforme SHALL retourner `{status: ok}`.
2. WHEN un client appelle `GET /ready`, THE Plateforme SHALL vérifier la disponibilité de
   PostgreSQL et de Redis avant de répondre.

### Exigence 28 — Minimisation des données et RGPD

**User Story:** En tant qu'exploitant de la Plateforme, je veux minimiser les données et respecter le
RGPD, afin de protéger les Utilisateurs et de rester conforme.

#### Critères d'acceptation

1. THE Plateforme SHALL s'abstenir de collecter la profession, l'orientation politique, la
   localisation précise et toute donnée personnelle non nécessaire.
2. THE Plateforme SHALL s'abstenir de constituer un profil politique individuel.
3. THE Plateforme SHALL publier les pages `/mentions-legales`, `/confidentialite`, `/cookies` et
   `/conditions`.
4. THE Plateforme SHALL fournir des fonctions d'export des données, de suppression de compte et de
   gestion du consentement.
5. THE Plateforme SHALL définir une politique de rétention explicite pour les comptes, les Votes,
   les journaux, les conversations IA et les signalements.
6. THE Plateforme SHALL s'abstenir de committer des secrets dans Git.

### Exigence 29 — Journal d'audit

**User Story:** En tant qu'exploitant de la Plateforme, je veux journaliser les actions sensibles,
afin d'assurer la traçabilité sans conserver de données identifiantes superflues.

#### Critères d'acceptation

1. WHEN une action sensible est effectuée, THE Plateforme SHALL enregistrer une entrée
   Journal_D_Audit avec action, entity_type, entity_id, old_data et new_data.
2. THE Plateforme SHALL enregistrer un ip_hash dans le Journal_D_Audit et SHALL s'abstenir de
   conserver l'adresse IP brute.

### Exigence 30 — Observabilité

**User Story:** En tant qu'exploitant de la Plateforme, je veux tracer les requêtes et les
opérations IA, afin de diagnostiquer et d'auditer le fonctionnement.

#### Critères d'acceptation

1. THE Plateforme SHALL produire une journalisation structurée avec identifiant de requête pour
   l'API, le Worker_Celery, la base de données et le Pipeline_RAG.
2. THE Plateforme SHALL rendre chaque requête IA retrouvable par request_id, user_id, timestamp,
   modèle et documents récupérés (retrieved_documents).
3. THE Plateforme SHALL soumettre le contenu des conversations IA à une politique de rétention
   explicite.

### Exigence 31 — API REST

**User Story:** En tant que client de l'API, je veux accéder aux entités de la Plateforme via des
points d'accès REST versionnés, afin d'alimenter le frontend et d'éventuelles intégrations.

#### Critères d'acceptation

1. THE API SHALL exposer sous `/api/v1` les points d'accès d'authentification (register, login,
   logout, refresh, me), Thèmes, Propositions, Votes, Arguments, Commentaires, Programme,
   statistiques, Sources et chat.
2. WHEN une requête API porte sur une ressource inexistante, THE API SHALL retourner un code
   d'erreur « ressource introuvable ».
3. IF une requête API modifiant l'état est reçue sans authentification valide, THEN THE API SHALL
   refuser la requête et retourner un code d'erreur d'authentification.
4. WHEN l'API reçoit une requête dont le corps est invalide, THE API SHALL retourner un code
   d'erreur de validation décrivant les champs en erreur.

### Exigence 32 — Infrastructure et démarrage

**User Story:** En tant qu'exploitant de la Plateforme, je veux démarrer l'ensemble des services de
manière reproductible, afin de disposer d'un environnement fonctionnel et testable.

#### Critères d'acceptation

1. WHEN un exploitant exécute `docker compose up`, THE Plateforme SHALL démarrer l'ensemble des
   services (API, Web, Worker_Celery, PostgreSQL/pgvector, Redis).
2. THE Plateforme SHALL persister les données PostgreSQL.
3. THE Plateforme SHALL rendre Redis fonctionnel.
4. THE Plateforme SHALL fournir des migrations reproductibles.
5. THE Plateforme SHALL fournir une suite de tests exécutable qui passe.
6. THE Plateforme SHALL s'assurer qu'aucun secret n'est présent dans Git.

### Exigence 33 — Architecture V1 (monolithe modulaire)

**User Story:** En tant qu'exploitant de la Plateforme, je veux une architecture de monolithe
modulaire, afin de livrer la V1 sans complexité de micro-services prématurée.

#### Critères d'acceptation

1. THE Plateforme SHALL être structurée en monolithe modulaire regroupant l'API, le Web, les
   Services, PostgreSQL/pgvector, Redis et le Worker_Celery, avec le RAG intégré au backend et au
   worker.
2. THE Plateforme SHALL s'abstenir d'adopter une architecture de micro-services au démarrage de la
   V1.
3. THE Plateforme SHALL préserver la trajectoire d'évolution : V1 monolithe modulaire → V2
   frontend séparé → V3 services IA/RAG spécialisés → V4 distribution si nécessaire.
