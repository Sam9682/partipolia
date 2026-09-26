# Requirements Document

## Introduction

Cette fonctionnalité étend le socle d'authentification déjà en place dans PARTIPOLAI (l'**Exigence 1** de la spec `partipolia-platform` : inscription, connexion par mot de passe, JWT access + refresh, cookies HttpOnly, `require_admin`, `AuditLog`). Elle **ne redéfinit pas** ce socle : elle s'appuie dessus et le complète sur quatre manques fonctionnels.

1. **Administration des Utilisateurs** — un Administrateur authentifié peut créer, lister, activer et désactiver des comptes. La désactivation bloque effectivement l'accès (les gardes `authenticate` / `refresh` / `current_user` rejettent déjà les comptes inactifs). Des garde-fous empêchent un Administrateur de se désactiver lui-même et de désactiver le dernier Administrateur actif.
2. **Contrôle d'accès au niveau des pages Web (SSR)** — les pages servies par `app/web/router.py` sont classées en pages publiques, pages réservées aux Utilisateurs authentifiés et pages réservées aux Administrateurs. Un accès non authentifié à une page protégée redirige vers la connexion ; un accès non administrateur à une page d'administration est interdit.
3. **Administrateur par défaut (seed)** — un Administrateur par défaut de login « admin » et de mot de passe « Admin1234! » est amorcé de façon **idempotente** au démarrage, sans jamais écraser un compte existant ni réinitialiser son mot de passe. Ce mot de passe est un identifiant de développement qui doit être changé en production.
4. **Connexion SSO (LinkedIn, Google, Facebook)** — connexion via OAuth2/OIDC. Au premier succès, un Utilisateur est provisionné en base (email fourni par le fournisseur, `display_name`, `is_active=True`, `is_verified` selon la vérification d'email du fournisseur, `is_admin=False`, `password_hash` aléatoire inutilisable). Aux connexions suivantes, le rattachement se fait par email vérifié. Le SSO émet exactement le même couple de jetons/cookies que la connexion par mot de passe, de sorte que le reste de l'application reste inchangé.

Le tout respecte les principes transverses déjà appliqués : messages d'erreur génériques (pas d'énumération de comptes), aucun secret journalisé, protection CSRF/`state`/`nonce` du flux OAuth, journalisation d'audit des actions sensibles (via `AuditLog`, Exigence 29 — `ip_hash` uniquement) et cohérence RGPD avec le champ `consents` existant (Exigence 28).

> **Note de numérotation.** Les Exigences ci-dessous sont numérotées à partir de 1 pour **cette** spec. Elles **prolongent** l'Exigence 1 de `partipolia-platform` (comptes et authentification) sans la dupliquer ; les références « Exigence 1.x » désignent le socle existant, les références « Exigence N » sans qualificatif désignent le présent document.

## Glossary

- **Plateforme** : L'application PARTIPOLAI (FastAPI async + SQLAlchemy 2.x async + PostgreSQL + Redis + Jinja2 SSR).
- **Utilisateur** : Un enregistrement de la table `users` (champs `id`, `email` UNIQUE, `password_hash`, `display_name`, `is_active`, `is_verified`, `is_admin`, `consents`, horodatages).
- **Administrateur** : Un Utilisateur dont `is_admin` vaut vrai. Seul un Administrateur passe la garde `require_admin`.
- **Utilisateur_Actif** : Un Utilisateur dont `is_active` vaut vrai.
- **Service_Administration_Utilisateurs** : Le composant applicatif qui crée, liste, active et désactive les Utilisateurs pour le compte d'un Administrateur.
- **API_Admin_Utilisateurs** : Les points d'accès REST d'administration des Utilisateurs, protégés par `require_admin`.
- **Page_Publique** : Une page Web SSR consultable sans authentification (ex. `/programme`, `/themes`, pages RGPD).
- **Page_Protegee** : Une page Web SSR réservée aux Utilisateurs authentifiés.
- **Page_Admin** : Une page Web SSR réservée aux Administrateurs.
- **Administrateur_Par_Defaut** : L'Administrateur amorcé au démarrage avec le login « admin ».
- **Seed_Admin** : L'opération idempotente qui garantit l'existence de l'Administrateur_Par_Defaut.
- **Fournisseur_SSO** : Un fournisseur d'identité OAuth2/OIDC parmi LinkedIn, Google et Facebook.
- **Service_SSO** : Le composant applicatif qui pilote le flux OAuth (redirection, `state`, `nonce`, échange de code, récupération du profil) et le provisionnement/rattachement d'Utilisateur.
- **Provisionnement_SSO** : La création d'un Utilisateur lors du premier succès de connexion via un Fournisseur_SSO.
- **Rattachement_SSO** : L'association d'une connexion SSO à un Utilisateur existant par email vérifié.
- **Couple_De_Jetons** : Le couple JWT access + refresh émis par `AuthService`, déposé dans les cookies HttpOnly `access_token` et `refresh_token` (Exigences 1.4, 1.5).
- **Journal_Audit** : Le modèle `AuditLog` (table `audit_logs`, Exigence 29) consignant les actions sensibles avec `ip_hash` uniquement.

## Requirements

### Requirement 1 — Administration des Utilisateurs (API)

**User Story:** En tant qu'Administrateur, je veux créer, lister, activer et désactiver des comptes Utilisateur, afin de gérer l'accès à la Plateforme.

#### Acceptance Criteria

1. WHERE la requête est authentifiée par un Administrateur, THE API_Admin_Utilisateurs SHALL exposer un point d'accès de création d'Utilisateur acceptant `email`, `display_name`, `is_admin` et un mot de passe, et créant un Utilisateur avec `is_active=True`.
2. IF une requête vers l'API_Admin_Utilisateurs provient d'un Utilisateur non authentifié, THEN THE Plateforme SHALL répondre par un statut `401` à message générique.
3. IF une requête vers l'API_Admin_Utilisateurs provient d'un Utilisateur authentifié non Administrateur, THEN THE Plateforme SHALL répondre par un statut `403` à message générique.
4. WHERE la requête est authentifiée par un Administrateur, THE API_Admin_Utilisateurs SHALL exposer un point d'accès de liste des Utilisateurs restituant pour chaque Utilisateur `id`, `email`, `display_name`, `is_active`, `is_verified` et `is_admin`, sans jamais inclure `password_hash`.
5. WHEN un Administrateur demande l'activation d'un Utilisateur, THE Service_Administration_Utilisateurs SHALL positionner `is_active` de cet Utilisateur à vrai.
6. WHEN un Administrateur demande la désactivation d'un Utilisateur, THE Service_Administration_Utilisateurs SHALL positionner `is_active` de cet Utilisateur à faux.
7. IF un Administrateur tente de créer un Utilisateur avec un `email` déjà enregistré, THEN THE Plateforme SHALL refuser la création par un message générique sans révéler l'existence du compte.

### Requirement 2 — Effet de la désactivation sur l'accès

**User Story:** En tant qu'exploitant de la Plateforme, je veux qu'un compte désactivé perde immédiatement l'accès, afin de bloquer un Utilisateur indésirable.

#### Acceptance Criteria

1. WHEN un Utilisateur désactivé tente de se connecter par mot de passe, THE Plateforme SHALL refuser la connexion par un message générique (conformément à l'Exigence 1.9).
2. WHILE un Utilisateur est désactivé, THE Plateforme SHALL refuser le rafraîchissement de jeton et la résolution de l'Utilisateur courant pour cet Utilisateur.
3. WHEN un Utilisateur désactivé présente un access token encore valide sur une Page_Protegee ou une Page_Admin, THE Plateforme SHALL lui refuser l'accès comme à un Utilisateur non authentifié.

### Requirement 3 — Garde-fous d'auto-désactivation et du dernier Administrateur

**User Story:** En tant qu'Administrateur, je veux être empêché de me verrouiller hors de l'administration, afin de préserver la continuité de la gestion de la Plateforme.

#### Acceptance Criteria

1. IF un Administrateur demande la désactivation de son propre compte, THEN THE Service_Administration_Utilisateurs SHALL refuser l'opération et laisser `is_active` inchangé.
2. IF une demande de désactivation vise le dernier Administrateur dont `is_active` vaut vrai, THEN THE Service_Administration_Utilisateurs SHALL refuser l'opération et laisser `is_active` inchangé.
3. WHEN une demande de désactivation vise un Administrateur alors qu'il reste au moins un autre Administrateur actif, THE Service_Administration_Utilisateurs SHALL réaliser la désactivation.

### Requirement 4 — Journalisation d'audit des actions d'administration

**User Story:** En tant qu'exploitant de la Plateforme, je veux tracer les actions d'administration des comptes, afin de disposer d'un historique auditable.

#### Acceptance Criteria

1. WHEN un Administrateur crée, active ou désactive un Utilisateur, THE Plateforme SHALL enregistrer une entrée dans le Journal_Audit précisant l'action, le type d'entité `user` et l'`entity_id` visé.
2. THE Plateforme SHALL enregistrer dans le Journal_Audit l'empreinte `ip_hash` de l'auteur de l'action et jamais l'adresse IP brute (conformément à l'Exigence 29.2).
3. THE Plateforme SHALL exclure tout mot de passe et tout `password_hash` des champs `old_data` et `new_data` du Journal_Audit.

### Requirement 5 — Contrôle d'accès des pages Web (SSR)

**User Story:** En tant que visiteur, je veux que chaque page Web applique l'accès correspondant à mon état de connexion et à mon rôle, afin que les contenus réservés restent protégés.

#### Acceptance Criteria

1. WHEN un visiteur consulte une Page_Publique, THE Plateforme SHALL rendre la page sans exiger d'authentification.
2. IF un Utilisateur non authentifié demande une Page_Protegee, THEN THE Plateforme SHALL répondre par une redirection vers la page de connexion.
3. WHEN un Utilisateur authentifié demande une Page_Protegee, THE Plateforme SHALL rendre la page.
4. IF un Utilisateur non authentifié demande une Page_Admin, THEN THE Plateforme SHALL répondre par une redirection vers la page de connexion.
5. IF un Utilisateur authentifié non Administrateur demande une Page_Admin, THEN THE Plateforme SHALL refuser l'accès par un statut `403`.
6. WHEN un Administrateur demande une Page_Admin, THE Plateforme SHALL rendre la page.

### Requirement 6 — Administrateur par défaut (seed idempotent)

**User Story:** En tant qu'exploitant de la Plateforme, je veux qu'un Administrateur par défaut existe après démarrage, afin de pouvoir administrer une instance neuve.

#### Acceptance Criteria

1. WHEN la Plateforme démarre et qu'aucun Administrateur_Par_Defaut n'existe, THE Seed_Admin SHALL créer un Utilisateur Administrateur (`is_admin=True`, `is_active=True`) correspondant au login « admin » avec le mot de passe par défaut « Admin1234! » stocké sous forme de `password_hash`.
2. IF l'Administrateur_Par_Defaut existe déjà au démarrage, THEN THE Seed_Admin SHALL laisser cet Utilisateur inchangé, sans réécrire son `password_hash` ni ses autres champs.
3. WHEN le Seed_Admin est exécuté plusieurs fois de suite, THE Plateforme SHALL maintenir exactement un Administrateur_Par_Defaut (opération idempotente).
4. THE Plateforme SHALL exposer le login, l'email et le mot de passe de l'Administrateur_Par_Defaut via des variables d'environnement, avec les valeurs par défaut « admin » / « Admin1234! » réservées au développement.
5. THE Plateforme SHALL s'abstenir de journaliser le mot de passe par défaut lors du Seed_Admin.

### Requirement 7 — Configuration des Fournisseurs SSO

**User Story:** En tant qu'exploitant de la Plateforme, je veux configurer LinkedIn, Google et Facebook par l'environnement, afin d'activer le SSO sans secret en dur.

#### Acceptance Criteria

1. THE Plateforme SHALL lire, pour chaque Fournisseur_SSO, l'identifiant client, le secret client et l'URI de redirection depuis des variables d'environnement, sans valeur par défaut exploitable pour le secret client.
2. THE Plateforme SHALL dériver l'URI de redirection de chaque Fournisseur_SSO à partir de `app_base_url` lorsque l'URI n'est pas fournie explicitement.
3. WHERE l'identifiant client ou le secret client d'un Fournisseur_SSO est absent, THE Plateforme SHALL considérer ce Fournisseur_SSO comme désactivé et ne pas exposer son point d'accès de connexion.
4. THE Plateforme SHALL s'abstenir de journaliser l'identifiant client, le secret client et les jetons reçus des Fournisseurs_SSO.

### Requirement 8 — Flux de connexion SSO et protection CSRF

**User Story:** En tant qu'Utilisateur, je veux me connecter via un Fournisseur_SSO, afin d'accéder à la Plateforme sans mot de passe local.

#### Acceptance Criteria

1. WHEN un Utilisateur initie une connexion via un Fournisseur_SSO activé, THE Service_SSO SHALL rediriger vers l'autorisation du fournisseur avec un paramètre `state` aléatoire à usage unique et, pour les fournisseurs OIDC, un `nonce`.
2. WHEN le Fournisseur_SSO redirige vers l'URI de rappel, THE Service_SSO SHALL vérifier la correspondance du `state` avant tout échange de code.
3. IF le paramètre `state` du rappel est absent, invalide ou déjà consommé, THEN THE Service_SSO SHALL refuser la connexion par un message générique.
4. WHEN le `state` est validé, THE Service_SSO SHALL échanger le code d'autorisation contre les jetons du fournisseur et récupérer le profil (email, nom d'affichage, état de vérification de l'email).
5. IF l'échange de code ou la récupération du profil échoue, THEN THE Service_SSO SHALL refuser la connexion par un message générique sans divulguer la cause précise.

### Requirement 9 — Provisionnement et rattachement d'Utilisateur par SSO

**User Story:** En tant qu'Utilisateur, je veux qu'une première connexion SSO crée mon compte et que les suivantes le retrouvent, afin de conserver une identité unique.

#### Acceptance Criteria

1. WHEN une connexion SSO aboutit pour un email vérifié sans Utilisateur correspondant, THE Service_SSO SHALL provisionner un Utilisateur avec `email` normalisé du fournisseur, `display_name` du fournisseur, `is_active=True`, `is_admin=False`, `is_verified` reflétant la vérification d'email du fournisseur, et un `password_hash` aléatoire inutilisable pour une connexion par mot de passe.
2. WHEN une connexion SSO aboutit pour un email correspondant à un Utilisateur existant, THE Service_SSO SHALL rattacher la connexion à cet Utilisateur existant sans créer de doublon.
3. IF le Fournisseur_SSO indique que l'email n'est pas vérifié, THEN THE Service_SSO SHALL refuser le Provisionnement_SSO et le Rattachement_SSO.
4. WHEN un Provisionnement_SSO ou un Rattachement_SSO aboutit, THE Service_SSO SHALL émettre le même Couple_De_Jetons et poser les mêmes cookies HttpOnly que la connexion par mot de passe (Exigences 1.4, 1.5).
5. IF le Rattachement_SSO vise un Utilisateur dont `is_active` vaut faux, THEN THE Service_SSO SHALL refuser la connexion par un message générique.
6. WHEN un Provisionnement_SSO est réalisé, THE Plateforme SHALL enregistrer une entrée dans le Journal_Audit précisant l'action de provisionnement, le type d'entité `user` et le Fournisseur_SSO utilisé, sans journaliser de jeton.

### Requirement 10 — Cohérence RGPD et minimisation des données SSO

**User Story:** En tant qu'exploitant de la Plateforme, je veux que le SSO respecte la minimisation des données et le consentement, afin de rester conforme au RGPD.

#### Acceptance Criteria

1. THE Service_SSO SHALL persister uniquement l'`email`, le `display_name` et l'état de vérification d'email fournis par le Fournisseur_SSO, sans stocker de donnée de profil superflue (conformément aux Exigences 28.1, 28.2).
2. WHEN un Utilisateur est provisionné par SSO, THE Plateforme SHALL initialiser son champ `consents` avec un objet de consentement vide cohérent avec les comptes créés par inscription classique (Exigence 28.4).
3. THE Plateforme SHALL s'abstenir de conserver les jetons d'accès des Fournisseurs_SSO au-delà de la durée nécessaire à la récupération du profil.
