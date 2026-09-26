# Implementation Plan: user-management-and-sso

## Overview

Étend le socle d'authentification existant (`AuthService`, `deps.py`, cookies HttpOnly,
`AuditLog`) avec quatre manques fonctionnels : administration des Utilisateurs (API +
page SSR), contrôle d'accès des pages Web SSR, amorçage idempotent d'un Administrateur
par défaut, et connexion SSO (Google, LinkedIn, Facebook). Le travail est ordonné en
partant du bas (migration + modèle) vers le haut (routes, pages, wiring), chaque étape
s'appuyant sur la précédente et se terminant intégrée à l'application. Les tests
property (`hypothesis`, ≥ 100 itérations) couvrent les 9 propriétés de correction du
design ; ils sont marqués optionnels (`*`) mais référencent chacun leur propriété.

Décision confirmée repliée dans le plan : ajout d'une page SSR **publique** minimale
`/connexion` (gabarit Jinja2) avec formulaire email/mot de passe postant vers
`/api/v1/auth/login` et boutons SSO pointant vers `/api/v1/auth/sso/{provider}/login`
pour chaque Fournisseur_SSO **activé** ; cette page est la cible de redirection de
`require_page_auth` / `require_page_admin`.

## Tasks

- [ ] 1. Migration Alembic `0003` et colonne `sso_provider` du modèle `User`
  - [ ] 1.1 Ajouter la colonne `sso_provider` au modèle `app/models/user.py`
    - Ajouter `sso_provider: Mapped[str | None]` (`String(32)`, `nullable=True`)
    - `password_hash` reste NON nul (inchangé)
    - _Requirements: 9.1, 9.2_

  - [ ] 1.2 Créer `alembic/versions/0003_user_sso_provider.py`
    - `down_revision = "0002_user_consents"`
    - `upgrade`: `op.add_column("users", sa.Column("sso_provider", sa.String(length=32), nullable=True))`
    - `downgrade`: `op.drop_column("users", "sso_provider")`
    - Docstring référençant les Exigences, réversibilité complète (style `0001`/`0002`)
    - _Requirements: 9.1, 9.2_

  - [ ]* 1.3 Écrire un test d'exemple/migration pour `sso_provider`
    - Introspecter `User`: `sso_provider` est `String(32)` nullable, `password_hash` reste non nul
    - Après `upgrade` la colonne existe ; après `downgrade` elle est absente
    - _Requirements: 9.1, 9.2_

- [ ] 2. Ajouts de configuration (`app/core/config.py`)
  - [ ] 2.1 Ajouter les champs Administrateur par défaut et SSO à `Settings`
    - `default_admin_email` / `default_admin_display_name` / `default_admin_password` (`SecretStr`, défaut « Admin1234! ») avec alias d'environnement (Exigence 6.4)
    - `{google,linkedin,facebook}_client_id` / `_client_secret` (`SecretStr`, défaut vide) / `_redirect_uri`
    - `sso_provider_config(name)` construisant `SsoProviderConfig`, `redirect_uri` vide dérivé de `app_base_url` → `{app_base_url}/api/v1/auth/sso/{name}/callback` (Exigence 7.2)
    - `is_sso_provider_enabled(name)` : vrai ssi `client_id` **et** `client_secret` présents (Exigence 7.3)
    - Étendre `_check_secrets_in_production` : refuser en production le mot de passe admin par défaut et un `JWT_SECRET_KEY` vide (Exigences 6.4)
    - _Requirements: 6.4, 7.1, 7.2, 7.3_

  - [ ]* 2.2 Écrire des tests unitaires de configuration
    - Dérivation de `redirect_uri` depuis `app_base_url` quand l'URI n'est pas fournie
    - `is_sso_provider_enabled` faux si `client_id` ou `client_secret` manquant, vrai sinon
    - Garde de production : mot de passe admin par défaut refusé en production
    - _Requirements: 6.4, 7.1, 7.2, 7.3_

- [ ] 3. Aides de sécurité (`app/core/security.py`)
  - [ ] 3.1 Créer `hash_client_ip` et `generate_unusable_password_hash`
    - `hash_client_ip(request)` : empreinte SHA-256 de l'IP source (`X-Forwarded-For` sinon `request.client`), jamais l'IP en clair, `None` si indisponible (Exigence 29.2)
    - `generate_unusable_password_hash(auth)` : hache un mot de passe aléatoire (`secrets.token_urlsafe`) jamais communiqué (Exigence 9.1)
    - _Requirements: 4.2, 9.1_

  - [ ]* 3.2 Écrire des tests unitaires des aides de sécurité
    - `hash_client_ip` ne renvoie jamais l'IP brute, préfère `X-Forwarded-For`, tolère l'absence de client
    - `generate_unusable_password_hash` produit un hachage qui échoue à `verify_password` contre toute valeur devinable, et diffère à chaque appel
    - _Requirements: 4.2, 9.1_

- [ ] 4. Schémas d'administration (`app/schemas/admin_user.py`)
  - [ ] 4.1 Définir `AdminUserCreate` et `UserAdminPublic`
    - `AdminUserCreate` : `email: EmailStr`, `display_name` (1–120), `password` (8–256), `is_admin: bool = False`
    - `UserAdminPublic` (`from_attributes=True`) : `id`, `email`, `display_name`, `is_active`, `is_verified`, `is_admin` — jamais `password_hash`
    - _Requirements: 1.1, 1.4, 4.3_

- [ ] 5. `UserAdminService` (`app/services/user_admin_service.py`)
  - [ ] 5.1 Implémenter `UserAdminService` avec garde-fous et audit
    - Exception dédiée `UserAdminError` (message générique)
    - `create_user` : normalise l'email (via `AuthService._normalize_email`), contrôle d'unicité + contrainte `UNIQUE`, `is_active=True`, `password_hash=auth.hash_password(...)`, `consents={}`, audit `admin.user.create` ; email déjà pris ⇒ `UserAdminError` générique (Exigences 1.1, 1.7)
    - `list_users` : tous les Utilisateurs projetés en `UserAdminPublic` (Exigence 4)
    - `activate` : `is_active=True`, audit `admin.user.activate` (Exigence 5)
    - `deactivate` : garde-fous **avant** écriture — refus si `user_id == actor_id` (3.1), refus si cible admin ET dernier `is_admin AND is_active` via `SELECT count(*)` (3.2), sinon `is_active=False` (3.3), audit `admin.user.deactivate`
    - Audit : `entity_type="user"`, `entity_id` cible, `ip_hash` fourni, `old_data`/`new_data` sans mot de passe ni `password_hash` (Exigence 4)
    - _Requirements: 1.1, 1.7, 3.1, 3.2, 3.3, 4.1, 4.3, 5.5, 5.6_

  - [ ]* 5.2 Écrire le test property : refus d'auto-désactivation
    - **Feature: user-management-and-sso, Property 2: Refus d'auto-désactivation**
    - Générer des Administrateurs variés ; `deactivate(id, actor_id=id)` est refusé et laisse `is_active` inchangé (≥ 100 itérations)
    - **Validates: Requirements 3.1**

  - [ ]* 5.3 Écrire le test property : préservation du dernier Administrateur actif
    - **Feature: user-management-and-sso, Property 3: Préservation du dernier Administrateur actif**
    - Générer des ensembles d'Utilisateurs ; désactiver le dernier admin actif est refusé ; s'il reste un autre admin actif distinct de l'acteur, la désactivation aboutit (≥ 100 itérations)
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 5.4 Écrire le test property : confidentialité des sorties d'administration
    - **Feature: user-management-and-sso, Property 4: Confidentialité des sorties d'administration**
    - Pour tout Utilisateur créé/listé, la représentation `UserAdminPublic` ne contient jamais `password_hash` (≥ 100 itérations)
    - **Validates: Requirements 1.4, 4.3**

  - [ ]* 5.5 Écrire le test property : absence de secret dans l'audit
    - **Feature: user-management-and-sso, Property 9: Absence de secret dans l'audit**
    - Pour toute action d'administration, `old_data`/`new_data` sans mot de passe ni `password_hash` ni jeton, et `ip_hash` jamais une IP en clair (≥ 100 itérations)
    - **Validates: Requirements 4.2, 4.3, 9.6**

- [ ] 6. API d'administration (`app/api/v1/admin_users.py`)
  - [ ] 6.1 Implémenter le routeur `/api/v1/admin/users`
    - `dependencies=[Depends(require_admin)]` + `default_rate_limit("admin_users")`
    - `POST /admin/users` (créer, 1.1/1.7), `GET /admin/users` (lister, 4), `POST /admin/users/{id}/activate` (5), `POST /admin/users/{id}/deactivate` (3, 6)
    - Dérive `ip_hash` via `hash_client_ip(request)` et le passe au Service
    - Traduit `UserAdminError` en `400` générique ; `401`/`403` génériques via les gardes ; `429` sur débit
    - Enregistrer dans l'API v1 : `api_router.include_router(admin_users.router, prefix="/admin/users", tags=["admin-users"])`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 3.1, 3.2, 3.3_

  - [ ]* 6.2 Écrire les tests api de l'administration des Utilisateurs
    - `401` générique non authentifié, `403` générique non-admin (Exigences 1.2, 1.3)
    - Création + liste : la réponse n'inclut jamais `password_hash` (Exigences 1.1, 1.4)
    - Activation/désactivation ; `400` sur email déjà pris et sur garde-fou violé ; `429` sur dépassement de débit
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 3.1, 3.2_

- [ ] 7. Amorçage de l'Administrateur par défaut (`app/core/seed.py` + `app/main.py`)
  - [ ] 7.1 Implémenter `seed_default_admin` et le brancher dans le `lifespan`
    - `seed_default_admin(session_factory, settings)` : recherche par `default_admin_email` ; si absent, crée `is_admin=True`, `is_active=True`, `display_name`, `password_hash=AuthService.hash_password(default_admin_password)`, `consents={}` ; sinon ne modifie rien (Exigences 6.1, 6.2, 6.3)
    - Ne journalise jamais le mot de passe : `admin_seed_created` / `admin_seed_present` uniquement (Exigence 6.5)
    - Rattrape `IntegrityError` concurrente sans écraser ; tolère l'absence de table (avertissement, pas d'échec de démarrage)
    - `app/main.py` : `await seed_default_admin()` au démarrage, après `configure_logging()`, avant `yield`
    - _Requirements: 6.1, 6.2, 6.3, 6.5_

  - [ ]* 7.2 Écrire le test property : idempotence du seed Administrateur
    - **Feature: user-management-and-sso, Property 1: Idempotence du seed Administrateur**
    - Pour toute base initiale (avec/sans admin), exécuter `seed_default_admin` une ou plusieurs fois laisse exactement un Utilisateur d'email `default_admin_email`, `password_hash` inchangé si préexistant (≥ 100 itérations)
    - **Validates: Requirements 6.2, 6.3**

- [ ] 8. Checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 9. Contrôle d'accès SSR et page de connexion `/connexion`
  - [ ] 9.1 Créer les dépendances d'accès SSR (`app/web/deps.py`)
    - `require_page_auth` : non authentifié → `RedirectResponse` vers `/connexion` (Exigence 5.2), sinon passe (5.3)
    - `require_page_admin` : non authentifié → redirection `/connexion` (5.4) ; authentifié non-admin → `HTTPException` `403` (5.5) ; Administrateur → passe (5.6)
    - Réutilise `get_current_user_optional` (compte désactivé traité comme non authentifié, Exigence 2.3)
    - _Requirements: 2.3, 5.2, 5.3, 5.4, 5.5, 5.6_

  - [ ] 9.2 Ajouter la page publique `/connexion` et son gabarit
    - Route publique dans `app/web/router.py` rendant `templates/connexion.html` (cohérent avec `base.html` et le motif `ssr_context`/`NAV_ITEMS`)
    - `connexion.html` : formulaire email/mot de passe postant vers `/api/v1/auth/login` + boutons SSO liés à `/api/v1/auth/sso/{provider}/login` pour chaque provider **activé** (`is_sso_provider_enabled`)
    - _Requirements: 5.1, 5.2_

  - [ ] 9.3 Ajouter la Page_Admin `/admin/utilisateurs`
    - Route dans `app/web/router.py` avec `dependencies=[Depends(require_page_admin)]`, rendant `templates/admin/utilisateurs.html` (liste/création/activation/désactivation consommant `/api/v1/admin/users`)
    - _Requirements: 5.4, 5.5, 5.6_

  - [ ]* 9.4 Écrire les tests api de contrôle d'accès SSR
    - `/admin/utilisateurs` non authentifié → redirection vers `/connexion` (5.4) ; authentifié non-admin → `403` (5.5) ; Administrateur → `200` (5.6)
    - `/connexion` public → `200` sans authentification et affiche les boutons SSO des providers activés (5.1)
    - _Requirements: 5.1, 5.4, 5.5, 5.6_

- [ ] 10. Service SSO (`app/schemas/sso.py` + `app/services/sso_service.py`)
  - [ ] 10.1 Définir `SsoProviderConfig` et le DTO `SsoProfile`
    - `SsoProviderConfig` (dataclass frozen) : `name`, `client_id`, `client_secret: SecretStr`, `redirect_uri`, `authorize_url`, `token_url`, `userinfo_url`, `scopes`, `uses_oidc`
    - `SsoProfile` (Pydantic) : `email: EmailStr`, `display_name: str`, `email_verified: bool` (minimisation, Exigence 10.1)
    - Câblage des paramètres Google/LinkedIn (OIDC → `nonce`) et Facebook (OAuth2)
    - _Requirements: 7.1, 10.1_

  - [ ] 10.2 Implémenter `SsoService.begin_login` et `complete_login`
    - `SsoError` générique (Exigences 8.3, 8.5, 9.5)
    - `begin_login` : `state`/`nonce` via `secrets.token_urlsafe`, stockés `sso:state:{state}` en Redis avec TTL, retourne l'URL d'autorisation (Exigence 8.1)
    - `complete_login` : validation `state` par `GETDEL` (usage unique, 8.2/8.3), échange de code via `httpx.AsyncClient` (8.4), vérification du `nonce` OIDC, lecture du profil (email/nom/vérifié), tout échec → `SsoError` générique (8.5)
    - Provisionnement/rattachement (Exigence 9) : email non vérifié → refus (9.3) ; email vérifié sans compte → provisionner (`is_active=True`, `is_admin=False`, `is_verified=<verified>`, `password_hash=generate_unusable_password_hash`, `consents={}`, `sso_provider=<provider>`) (9.1) ; email vérifié avec compte → rattacher sans doublon, met à jour `sso_provider` (9.2) ; compte inactif → refus (9.5) ; succès → `auth._issue_token_pair(user)` (9.4)
    - Audit `sso.user.provision` (`entity_type="user"`, `new_data={"provider": ...}`, `ip_hash`), aucun jeton journalisé, jetons fournisseur non persistés (Exigences 9.6, 7.4, 10.3)
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6, 10.1, 10.2, 10.3_

  - [ ]* 10.3 Écrire le test property : consommation unique du `state` SSO
    - **Feature: user-management-and-sso, Property 5: Consommation unique du `state` SSO**
    - Pour toute valeur de `state`, la première validation réussit au plus une fois ; toute réutilisation est refusée par un message générique (≥ 100 itérations)
    - **Validates: Requirements 8.2, 8.3**

  - [ ]* 10.4 Écrire le test property : identité unique par email vérifié
    - **Feature: user-management-and-sso, Property 6: Identité unique par email vérifié**
    - Pour toute connexion SSO d'email vérifié, la première provisionne et les suivantes rattachent au même Utilisateur sans doublon d'email (≥ 100 itérations)
    - **Validates: Requirements 9.1, 9.2**

  - [ ]* 10.5 Écrire le test property : refus SSO sur email non vérifié ou compte inactif
    - **Feature: user-management-and-sso, Property 7: Refus SSO sur email non vérifié ou compte inactif**
    - Pour toute connexion dont l'email n'est pas vérifié, ou visant un Utilisateur `is_active=False`, la connexion est refusée (message générique) et aucun Couple_De_Jetons n'est émis (≥ 100 itérations)
    - **Validates: Requirements 9.3, 9.5**

- [ ] 11. Routes SSO (`app/api/v1/auth_sso.py`)
  - [ ] 11.1 Implémenter `/api/v1/auth/sso/{provider}/login` et `/callback`
    - `GET .../login` : provider inconnu/désactivé → `404` (Exigence 7.3), sinon `RedirectResponse` vers l'URL d'autorisation (`begin_login`, 8.1)
    - `GET .../callback` : `complete_login` ; succès → `_set_access_cookie` + `_set_refresh_cookie` sur `RedirectResponse` vers `/programme` (9.4) ; échec → redirection `/connexion` message générique (8.3, 8.5, 9.5)
    - `default_rate_limit("sso")` ; enregistrer le routeur dans l'API v1
    - _Requirements: 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 9.4, 9.5_

  - [ ]* 11.2 Écrire le test property : équivalence du Couple_De_Jetons SSO / mot de passe
    - **Feature: user-management-and-sso, Property 8: Équivalence du Couple_De_Jetons SSO / mot de passe**
    - Pour tout provisionnement/rattachement SSO abouti, le Couple_De_Jetons émis a la même structure (claims `sub`/`type`/`jti`/`exp`, cookies HttpOnly access+refresh) que la connexion par mot de passe (≥ 100 itérations)
    - **Validates: Requirements 9.4**

- [ ] 12. Tests d'intégration SSO avec fournisseur simulé
  - [ ]* 12.1 Écrire les tests d'intégration du flux SSO de bout en bout
    - Fournisseur simulé (client `httpx` mocké) : `state` valide/invalide/rejoué, email vérifié/non vérifié, provisionnement puis rattachement au même compte, cookies HttpOnly posés au callback
    - `404` sur provider non configuré ; audit `sso.user.provision` enregistré sans jeton
    - Couvre les Propriétés 5–8 en scénarios concrets
    - _Requirements: 7.3, 8.1, 8.2, 8.3, 8.4, 8.5, 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

- [ ] 13. Câblage final
  - [ ] 13.1 Ajouter les variables `.env.example` et une note README
    - `.env.example` : `DEFAULT_ADMIN_EMAIL`/`DEFAULT_ADMIN_DISPLAY_NAME`/`DEFAULT_ADMIN_PASSWORD` et `{GOOGLE,LINKEDIN,FACEBOOK}_CLIENT_ID`/`_CLIENT_SECRET`/`_REDIRECT_URI`
    - Note README : identifiants de l'Administrateur par défaut (« admin » / « Admin1234! ») à changer en production
    - _Requirements: 6.4, 7.1_

- [ ] 14. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Les sous-tâches marquées `*` sont des tests optionnels et peuvent être ignorées pour un MVP plus rapide.
- Chaque tâche référence des Exigences précises pour la traçabilité.
- Les checkpoints assurent une validation incrémentale.
- Les tests property valident les propriétés de correction universelles (≥ 100 itérations) et référencent les Correctness Properties du design par numéro.
- Les tests unit/example et intégration valident des faits structurels et des scénarios concrets ; le SSO est testé via un fournisseur simulé (pas de vrai fournisseur OAuth).

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "2.1", "3.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.2", "3.2", "4.1"] },
    { "id": 2, "tasks": ["5.1", "10.1"] },
    { "id": 3, "tasks": ["5.2", "5.3", "5.4", "5.5", "6.1", "7.1", "10.2"] },
    { "id": 4, "tasks": ["6.2", "7.2", "9.1", "10.3", "10.4", "10.5", "11.1"] },
    { "id": 5, "tasks": ["9.2", "11.2"] },
    { "id": 6, "tasks": ["9.3"] },
    { "id": 7, "tasks": ["9.4", "12.1"] },
    { "id": 8, "tasks": ["13.1"] }
  ]
}
```
