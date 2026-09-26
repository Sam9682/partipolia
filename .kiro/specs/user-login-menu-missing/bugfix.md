# Bugfix Requirements Document

## Introduction

Le backend d'authentification de PARTIPOLAI est entièrement implémenté et exposé
sous `/api/v1/auth` (inscription, connexion, rafraîchissement, déconnexion,
compte courant), y compris le dépôt de jetons en cookies HttpOnly pour
l'application web. Pourtant, aucun point d'entrée de connexion n'est accessible
depuis l'interface : la barre de navigation d'en-tête rendue par `base.html` est
pilotée par la source de vérité serveur `NAV_ITEMS` (dans `app/web/router.py`),
qui ne contient que huit entrées (Programme, Thèmes, Propositions, Statistiques,
Équipes, Mandat, Assistant, API). Il n'existe ni entrée « Connexion » dans ce
menu, ni route SSR `/login`, ni gabarit `login.html`.

Conséquence : un visiteur ne peut ni voir ni atteindre une page de connexion
depuis l'UI, alors que la gestion des utilisateurs est fonctionnelle côté
backend. Ce bug empêche l'authentification et l'accès à la gestion des
utilisateurs par les moyens normaux de l'interface.

## Bug Analysis

### Current Behavior (Defect)

Ce qui se produit actuellement dans l'interface Web.

1.1 WHEN un visiteur consulte n'importe quelle page SSR (ex. `/programme`) THEN le système affiche une barre de navigation d'en-tête qui ne contient aucune entrée de connexion (Connexion / Se connecter / Compte)
1.2 WHEN un visiteur tente d'atteindre une page de connexion via l'interface THEN le système ne propose aucun lien ni point d'entrée, la seule route existante étant l'API `POST /api/v1/auth/login` non exposée dans la navigation
1.3 WHEN un visiteur navigue directement vers `/login` THEN le système renvoie une erreur `404 NOT_FOUND` car aucune route SSR `/login` n'est enregistrée dans `app/web/router.py`

### Expected Behavior (Correct)

Ce qui devrait se produire à la place.

2.1 WHEN un visiteur consulte n'importe quelle page SSR THEN le système SHALL afficher dans la barre de navigation d'en-tête une entrée de connexion (« Connexion ») pointant vers une cible résolvable
2.2 WHEN un visiteur clique sur l'entrée de connexion depuis l'interface THEN le système SHALL rendre une page de connexion accessible qui permet de saisir des identifiants et de s'authentifier via le backend `auth` existant
2.3 WHEN un visiteur navigue vers `/login` THEN le système SHALL rendre une page de connexion (réponse `200`) au lieu d'une erreur `404`

### Unchanged Behavior (Regression Prevention)

Comportements existants qui doivent être préservés.

3.1 WHEN un visiteur consulte une page SSR THEN le système SHALL CONTINUE TO afficher les huit entrées de navigation existantes (Programme, Thèmes, Propositions, Statistiques, Équipes, Mandat, Assistant, API) dans leur ordre actuel
3.2 WHEN un visiteur consulte une page publique (ex. `/programme`, `/themes`, pages RGPD) THEN le système SHALL CONTINUE TO servir cette page sans exiger d'authentification
3.3 WHEN un client appelle les points d'accès API `/api/v1/auth/*` (register, login, refresh, logout, me) THEN le système SHALL CONTINUE TO se comporter comme aujourd'hui, y compris les messages d'erreur génériques et le dépôt de cookies HttpOnly
3.4 WHEN aucune entrée `nav_items` n'est fournie au gabarit THEN le système SHALL CONTINUE TO appliquer le repli sûr affichant au moins le lien « Programme »

## Bug Condition and Properties

### Bug Condition

```pascal
FUNCTION isBugCondition(X)
  INPUT: X of type UiNavigationRequest   // requête de rendu d'une page SSR ou navigation UI
  OUTPUT: boolean

  // Le bug se manifeste quand l'utilisateur cherche à se connecter depuis l'UI :
  // aucune entrée de connexion dans le menu, et aucune page /login résolvable.
  RETURN (X.wants_login_entry_point = true)
END FUNCTION
```

### Property: Fix Checking

```pascal
// Property: Fix Checking — Point d'entrée de connexion présent et résolvable
FOR ALL X WHERE isBugCondition(X) DO
  navbar ← renderHeaderNav'(X)
  ASSERT hasLoginEntry(navbar) = true

  page ← route'("/login")
  ASSERT page.status = 200 AND rendersLoginForm(page) = true
END FOR
```

### Property: Preservation Checking

```pascal
// Property: Preservation Checking — Tout le reste inchangé
FOR ALL X WHERE NOT isBugCondition(X) DO
  ASSERT F(X) = F'(X)
END FOR
```

Où **F** est l'application avant le correctif et **F'** l'application après. Pour
toute requête ne cherchant pas le point d'entrée de connexion (rendu des pages
existantes, accès public sans authentification, appels API `auth`), le
comportement doit rester identique.
