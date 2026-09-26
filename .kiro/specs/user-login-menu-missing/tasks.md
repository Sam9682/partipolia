# Implementation Plan

Le correctif est strictement limité à la couche SSR/web (`app/web/router.py` et
`app/templates/login.html`). Aucun fichier `app/api/v1/*` ni aucun Service n'est
modifié. Les tests réutilisent les utilitaires observation-first de
`tests/api/test_navigation_and_styling_preservation.py` (montage de `web_router`
via `TestClient`, doublure `_FakeProgramService`, parsers HTML de `<nav>`).

- [x] 1. Écrire le test d'exploration de la condition de bug
  - **Property 1: Bug Condition** - Point d'entrée de connexion absent et `/login` non résolvable
  - **CRITICAL** : ce test DOIT ÉCHOUER sur le code non corrigé — l'échec confirme que le bug existe
  - **DO NOT** tenter de corriger le test ni le code quand il échoue à cette étape
  - **NOTE** : ce test encode le comportement attendu — il validera le correctif quand il passera après implémentation (voir tâche 3.2)
  - **GOAL** : faire émerger des contre-exemples démontrant l'absence du point d'entrée de connexion
  - **Scoped PBT Approach** : le bug est déterministe ; portée du test aux cas concrets reproductibles suivants, formulés en property-based (Hypothesis) sur l'ensemble des chemins SSR publics échantillonnés (`/programme`, `/themes`, `/propositions`, `/statistiques`, `/equipes`, `/mandat`, `/assistant`, pages RGPD) pour la partie « présence du lien »
  - Créer `tests/api/test_login_menu_bug.py` montant `web.web_router` via `TestClient` avec la doublure `_FakeProgramService` (réutiliser le motif de `test_navigation_and_styling_preservation.py`)
  - Cas 1 — Absence d'entrée « Connexion » : pour tout chemin SSR public échantillonné, parser le `<nav>` d'en-tête et assert la présence d'un lien de menu (`<a href>`) vers `/login` de libellé « Connexion » (échoue sur le code non corrigé — `NAV_ITEMS` n'a que 8 entrées)
  - Cas 2 — Route `/login` manquante : `GET /login` doit répondre `200` (échoue sur le code non corrigé — actuellement `404 NOT_FOUND`)
  - Cas 3 — Gabarit de connexion : la réponse de `/login` doit être `text/html` et contenir un `<form>` ciblant `/api/v1/auth/login` avec un champ email (`type="email"`) et un champ mot de passe (`type="password"`) (échoue tant que `login.html` n'existe pas)
  - Exécuter les tests sur le code NON corrigé
  - **EXPECTED OUTCOME** : les tests ÉCHOUENT (correct — cela prouve que le bug existe)
  - Documenter les contre-exemples : aucun `<a>` ne pointe vers `/login`, `GET /login` → `404 NOT_FOUND`, aucun gabarit `login.html`
  - Marquer la tâche terminée quand le test est écrit, exécuté et l'échec documenté
  - _Bug_Condition: isBugCondition(X) where X.wants_login_entry_point = true_
  - _Requirements: 1.1, 1.2, 1.3, 2.1, 2.2, 2.3_

- [x] 2. Écrire les tests de préservation basés sur les propriétés (AVANT le correctif)
  - **Property 2: Preservation** - Comportement inchangé hors condition de bug
  - **IMPORTANT** : suivre la méthodologie observation-first — relever la ligne de base sur le code NON corrigé, puis l'affirmer
  - Créer `tests/api/test_login_menu_preservation.py` en réutilisant les utilitaires de `test_navigation_and_styling_preservation.py` (montage `web_router` + `/static`, doublure `_FakeProgramService`, parsers HTML de `<nav>`, application d'échantillon `/api/v1/*`)
  - Observer sur le code non corrigé : les huit `NAV_ITEMS` (labels + href, dans l'ordre) — Programme `/programme`, Thèmes `/themes`, Propositions `/propositions`, Statistiques `/statistiques`, Équipes `/equipes`, Mandat `/mandat`, Assistant `/docs`... noter les valeurs exactes ; les statuts/types des pages SSR publiques ; les réponses d'un échantillon `/api/v1/*`
  - Property (préservation de l'ordre) : pour tout chemin SSR public échantillonné, le `<nav>` d'en-tête expose les huit entrées existantes **dans le même ordre** (labels + href), comparé à la ligne de base observée
  - Property (accès public) : pour tout chemin SSR public échantillonné (`/programme`, `/themes`, `/propositions`, `/statistiques`, `/equipes`, `/mandat`, `/assistant`, pages RGPD), la réponse est `200` en `text/html` sans authentification
  - Property (endpoints `auth`/`/api/v1/*` préservés) : pour un échantillon d'endpoints, statut et forme de réponse restent identiques à la ligne de base (ex. `POST /api/v1/auth/login` avec identifiants invalides ⇒ `401` générique)
  - Cas repli `nav_items` : le rendu de `base.html` sans `nav_items` affiche toujours au moins le lien « Programme »
  - Exécuter les tests sur le code NON corrigé
  - **EXPECTED OUTCOME** : les tests PASSENT (confirme la ligne de base à préserver)
  - Marquer la tâche terminée quand les tests sont écrits, exécutés et passants sur le code non corrigé
  - _Preservation: Preservation Requirements from design (huit entrées et ordre, accès public sans auth, endpoints /api/v1/auth/*, repli base.html)_
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. Correctif pour l'absence de point d'entrée de connexion dans l'UI

  - [x] 3.1 Implémenter le correctif (couche SSR/web uniquement)
    - Dans `app/web/router.py`, ajouter **en fin** de `NAV_ITEMS` la neuvième entrée `{"label": "Connexion", "href": "/login"}`, en préservant les huit entrées existantes et leur ordre
    - Dans `app/web/router.py`, enregistrer une route SSR **publique** `GET /login` (à l'image des autres vues SSR, sans authentification) rendant `login.html` via `templates.TemplateResponse(request, "login.html", ssr_context())` et renvoyant `200`
    - Créer `app/templates/login.html` héritant de `base.html` (en-tête, pied de page et repli de navigation communs) avec un `<form>` : champ `email` (`type="email"`), champ `password` (`type="password"`), bouton de soumission
    - Câbler le formulaire sur le backend existant `POST /api/v1/auth/login` (corps JSON `{email, password}`) ; en succès, le backend dépose les cookies HttpOnly puis l'UI redirige vers une page publique (ex. `/programme`) ; en échec `401` générique, afficher un message d'erreur générique sans révéler l'existence du compte
    - Ne modifier aucun fichier `app/api/v1/*` ni aucun Service
    - _Bug_Condition: isBugCondition(X) where X.wants_login_entry_point = true_
    - _Expected_Behavior: renderHeaderNav'(X) expose « Connexion » résolvable ET route'("/login") → status 200 avec formulaire de connexion (from design)_
    - _Preservation: huit entrées et ordre, accès public sans auth, /api/v1/auth/*, repli base.html (from design)_
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.2 Vérifier que le test d'exploration de la condition de bug passe désormais
    - **Property 1: Expected Behavior** - Point d'entrée de connexion présent et résolvable
    - **IMPORTANT** : ré-exécuter le MÊME test de la tâche 1 — ne PAS écrire de nouveau test
    - Le test de la tâche 1 encode le comportement attendu ; quand il passe, il confirme que le point d'entrée « Connexion » est présent et que `GET /login` rend le formulaire avec statut `200`
    - Exécuter `tests/api/test_login_menu_bug.py`
    - **EXPECTED OUTCOME** : le test PASSE (confirme que le bug est corrigé)
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 Vérifier que les tests de préservation passent toujours
    - **Property 2: Preservation** - Comportement inchangé hors condition de bug
    - **IMPORTANT** : ré-exécuter les MÊMES tests de la tâche 2 — ne PAS écrire de nouveaux tests
    - Exécuter `tests/api/test_login_menu_preservation.py`
    - **EXPECTED OUTCOME** : les tests PASSENT (confirme l'absence de régression : huit entrées inchangées et dans l'ordre, la neuvième « Connexion » venant après ; accès public préservé ; endpoints `auth` inchangés ; repli `base.html` intact)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. Checkpoint - S'assurer que tous les tests passent
  - Exécuter l'ensemble des tests de la spec (`tests/api/test_login_menu_bug.py`, `tests/api/test_login_menu_preservation.py`) et la suite pertinente pour détecter toute régression
  - S'assurer que tous les tests passent ; en cas de question ou d'ambiguïté, solliciter l'utilisateur
