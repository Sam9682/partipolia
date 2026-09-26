# user-login-menu-missing Bugfix Design

## Overview

Le backend d'authentification (`app/api/v1/auth.py`, sous `/api/v1/auth`) est
complet et fonctionnel : inscription, connexion, rafraîchissement, déconnexion,
compte courant, avec dépôt de jetons en cookies HttpOnly/Secure/SameSite=Lax
pour l'application web. Le défaut se situe **uniquement dans la couche SSR/web** :
il n'existe aucun point d'entrée de connexion dans l'interface.

Concrètement, trois manques dans `app/web/router.py` et `app/templates` :

1. `NAV_ITEMS` compte huit entrées (Programme, Thèmes, Propositions,
   Statistiques, Équipes, Mandat, Assistant, API) et aucune entrée « Connexion ».
2. Aucune route SSR `/login` n'est enregistrée → `GET /login` renvoie `404`.
3. Aucun gabarit `login.html` n'existe.

**Stratégie de correction** (minimale, ciblée, sans toucher au backend) :

- Ajouter une neuvième entrée `{"label": "Connexion", "href": "/login"}` à la
  fin de `NAV_ITEMS`, préservant les huit entrées existantes et leur ordre.
- Enregistrer une route SSR `GET /login` publique qui rend un gabarit
  `login.html` (réponse `200`) via l'aide partagée `ssr_context()`.
- Créer `app/templates/login.html` (héritant de `base.html`) présentant un
  formulaire de connexion câblé sur le backend existant `POST /api/v1/auth/login`
  (corps JSON `{email, password}`, session par cookie HttpOnly déposé par le
  backend). Aucune modification de `app/api/v1/*`.

Le correctif n'écrit rien, ne touche aucun Service ni aucun endpoint API, et
respecte la contrainte « aucun lien mort » : la nouvelle entrée pointe vers une
page SSR réellement montée.

## Glossary

- **Bug_Condition (C)** : condition qui déclenche le bug — l'utilisateur cherche
  un point d'entrée de connexion depuis l'UI (`X.wants_login_entry_point = true`),
  qui n'existe ni dans le menu ni comme route `/login`.
- **Property (P)** : comportement désiré pour C — la barre de navigation expose
  une entrée « Connexion » résolvable, et `GET /login` rend un formulaire de
  connexion avec statut `200`.
- **Preservation** : tout comportement hors condition de bug qui doit rester
  strictement inchangé — les huit entrées de navigation existantes et leur ordre,
  l'accès public sans authentification aux pages existantes, et le comportement
  des endpoints `/api/v1/auth/*`.
- **NAV_ITEMS** : la source de vérité serveur unique (liste ordonnée de dicts
  `{"label", "href"}`, dans `app/web/router.py`) qui pilote le menu d'en-tête
  rendu par `base.html`.
- **ssr_context** : l'aide partagée (dans `app/web/router.py`) qui injecte
  `nav_items` (copie de `NAV_ITEMS`) dans le contexte de chaque page SSR.
- **base.html** : le gabarit de base rendant l'en-tête piloté par `nav_items`,
  avec repli sûr affichant au moins « Programme » si `nav_items` est absent/vide.
- **/api/v1/auth/login** : endpoint backend existant, inchangé — corps JSON
  `{email, password}`, renvoie un `TokenPair` et dépose les cookies HttpOnly ;
  identifiants invalides ⇒ `401` à message générique.

## Bug Details

### Bug Condition

Le bug se manifeste quand un visiteur cherche à se connecter depuis l'interface
Web : la barre de navigation d'en-tête (pilotée par `NAV_ITEMS` via `ssr_context`
et rendue par `base.html`) ne contient aucune entrée de connexion, et aucune
route SSR `/login` n'est enregistrée dans `app/web/router.py` (donc `GET /login`
renvoie `404 NOT_FOUND`). Aucun gabarit `login.html` n'existe non plus.

**Formal Specification :**
```
FUNCTION isBugCondition(input)
  INPUT: input of type UiNavigationRequest   // rendu d'une page SSR ou navigation UI
  OUTPUT: boolean

  // Le visiteur cherche un point d'entrée de connexion depuis l'UI.
  RETURN input.wants_login_entry_point = true
END FUNCTION
```

### Examples

- **Menu sans connexion** : un visiteur ouvre `/programme` ; l'en-tête affiche
  les huit liens (Programme … API) — attendu : une entrée « Connexion » visible ;
  actuel : aucune entrée de connexion.
- **Aucun point d'entrée UI** : un visiteur cherche à s'authentifier ; le seul
  moyen est l'API `POST /api/v1/auth/login`, non exposée dans la navigation —
  attendu : un lien « Connexion » menant à une page de saisie d'identifiants ;
  actuel : aucun lien ni page.
- **Navigation directe `/login`** : un visiteur ouvre `/login` — attendu :
  page de connexion rendue (`200`) ; actuel : `404 NOT_FOUND`.
- **Cas limite `nav_items` absent** : si aucun `nav_items` n'est fourni au
  gabarit, `base.html` applique déjà le repli « Programme » — comportement
  préservé (aucune régression introduite par le correctif).

## Expected Behavior

### Preservation Requirements

**Comportements inchangés :**
- Les huit entrées de navigation existantes (Programme, Thèmes, Propositions,
  Statistiques, Équipes, Mandat, Assistant, API) restent présentes **dans leur
  ordre actuel**.
- Les pages publiques existantes (`/programme`, `/themes`, `/propositions`,
  `/statistiques`, `/equipes`, `/mandat`, `/assistant`, pages RGPD) continuent
  d'être servies **sans authentification**.
- Les endpoints `/api/v1/auth/*` (register, login, refresh, logout, me)
  continuent de se comporter à l'identique : messages d'erreur génériques,
  dépôt des cookies HttpOnly, formes de réponse et statuts inchangés.
- Le repli sûr de `base.html` (afficher au moins « Programme » quand `nav_items`
  est absent/vide) reste effectif.

**Portée :**
Toute requête qui **ne** cherche **pas** le point d'entrée de connexion doit
être totalement inaffectée par le correctif. Cela inclut :
- Le rendu des pages SSR existantes (hors le seul ajout d'un lien de menu).
- L'accès public sans authentification aux pages existantes.
- Les appels aux endpoints `/api/v1/auth/*` et aux autres endpoints `/api/v1/*`.

**Note :** le comportement correct attendu pour la condition de bug est défini
dans la section Correctness Properties (Property 1). Cette section-ci précise ce
qui ne doit **pas** changer.

## Hypothesized Root Cause

D'après l'analyse des exigences et du code, la cause est établie (elle n'est pas
seulement hypothétique) : le défaut est un manque dans la couche SSR/web.

1. **Entrée de menu absente** : `NAV_ITEMS` dans `app/web/router.py` ne déclare
   aucune entrée de connexion. Le menu étant piloté par cette source de vérité
   unique, aucune page ne peut afficher de lien « Connexion ».

2. **Route SSR `/login` non enregistrée** : `app/web/router.py` n'expose aucun
   gestionnaire `GET /login`, d'où le `404 NOT_FOUND` en navigation directe.

3. **Gabarit `login.html` inexistant** : `app/templates` ne contient aucun
   gabarit de connexion à rendre.

4. **Aucune régression backend** : le backend `auth` est complet ; le correctif
   n'a pas à le modifier, seulement à le rendre atteignable depuis l'UI.

## Correctness Properties

Property 1: Bug Condition - Point d'entrée de connexion présent et résolvable

_For any_ input where the bug condition holds (isBugCondition returns true —
le visiteur cherche un point d'entrée de connexion), the fixed SSR layer SHALL
afficher dans la barre de navigation d'en-tête une entrée « Connexion » pointant
vers une cible résolvable, ET rendre `GET /login` avec un statut `200` présentant
un formulaire de connexion câblé sur le backend `POST /api/v1/auth/login`.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - Tout le reste inchangé

_For any_ input where the bug condition does NOT hold (isBugCondition returns
false), the fixed application SHALL produce the same result as the original
application, préservant : les huit entrées de navigation existantes et leur
ordre, l'accès public sans authentification aux pages existantes, le
comportement des endpoints `/api/v1/auth/*` (et des autres `/api/v1/*`), ainsi
que le repli sûr « Programme » de `base.html`.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

La cause étant établie, les changements sont ciblés et minimaux.

**Fichier** : `app/web/router.py`

**Élément** : source de vérité `NAV_ITEMS` et nouvelle vue SSR

**Changements spécifiques** :
1. **Ajout de l'entrée de navigation « Connexion »** : ajouter, **en fin** de
   `NAV_ITEMS`, l'entrée `{"label": "Connexion", "href": "/login"}`. Les huit
   entrées existantes et leur ordre restent inchangés (la nouvelle entrée est la
   neuvième). Le menu, piloté par `ssr_context` → `nav_items`, expose alors le
   lien « Connexion » de manière identique sur toutes les pages SSR.

2. **Enregistrement de la route SSR `GET /login`** : ajouter une vue publique
   (sans authentification, à l'image des autres vues SSR) rendant
   `login.html` via `templates.TemplateResponse(request, "login.html", ssr_context())`.
   La cible reste ainsi résolvable (aucun lien mort) et renvoie `200`.

**Fichier** : `app/templates/login.html` (nouveau)

**Élément** : gabarit de la page de connexion

**Changements spécifiques** :
3. **Création du gabarit `login.html`** : hérite de `base.html` (donc en-tête,
   pied de page et repli de navigation communs). Présente un formulaire de
   connexion avec champs `email` (type email) et `password` (type password) et
   un bouton de soumission.

4. **Câblage sur le backend existant** : le formulaire soumet les identifiants à
   `POST /api/v1/auth/login` avec un corps JSON `{email, password}` (contrat de
   `LoginRequest`). En succès, le backend dépose les cookies HttpOnly (session
   web) ; l'UI redirige alors vers une page publique (ex. `/programme`). En
   échec (`401` générique), le formulaire affiche un message d'erreur générique
   sans révéler l'existence du compte, cohérent avec le backend. Aucun fichier
   `app/api/v1/*` n'est modifié.

5. **Aucune modification du backend `auth`** : le correctif se limite à la
   couche SSR/présentation ; les endpoints, schémas, messages génériques et
   cookies HttpOnly restent tels quels.

## Testing Strategy

### Validation Approach

Approche en deux temps : d'abord faire émerger des contre-exemples démontrant le
bug sur le code NON corrigé (menu sans « Connexion », `/login` → `404`), puis
vérifier que le correctif rend le point d'entrée présent et résolvable tout en
préservant l'existant. La préservation suit la méthodologie *observation-first*
déjà employée dans `tests/api/test_navigation_and_styling_preservation.py` :
relever la ligne de base sur le code non corrigé, puis l'affirmer.

### Exploratory Bug Condition Checking

**Goal** : faire émerger des contre-exemples démontrant le bug AVANT le correctif
et confirmer la cause établie. Si la cause était réfutée, il faudrait
re-hypothéser.

**Test Plan** : sur le code NON corrigé, exercer la couche SSR (client de test
montant `web_router` comme dans `test_navigation_and_styling_preservation.py`) et
observer les échecs.

**Test Cases** :
1. **Absence d'entrée « Connexion »** : rendre une page SSR (ex. `/programme`) et
   assert la présence d'un lien de menu vers `/login` (échouera sur le code non
   corrigé).
2. **Route `/login` manquante** : `GET /login` doit répondre `200` (échouera sur
   le code non corrigé — actuellement `404`).
3. **Gabarit de connexion** : la réponse de `/login` doit contenir un formulaire
   ciblant `/api/v1/auth/login` avec des champs email/mot de passe (échouera tant
   que le gabarit n'existe pas).
4. **Cas limite `nav_items` absent** : vérifier que le repli `base.html` affiche
   « Programme » (déjà vrai — sert de garde-fou anti-régression).

**Expected Counterexamples** :
- Aucun lien de menu ne pointe vers `/login`.
- `GET /login` renvoie `404 NOT_FOUND`.
- Causes : entrée absente de `NAV_ITEMS`, route SSR non enregistrée, gabarit
  `login.html` inexistant.

### Fix Checking

**Goal** : vérifier que, pour toute entrée où la condition de bug est vraie, le
correctif produit le comportement attendu (Property 1).

**Pseudocode :**
```
FOR ALL input WHERE isBugCondition(input) DO
  navbar := renderHeaderNav_fixed(input)
  ASSERT hasLoginEntry(navbar) = true

  page := route_fixed("/login")
  ASSERT page.status = 200 AND rendersLoginForm(page) = true
END FOR
```

### Preservation Checking

**Goal** : vérifier que, pour toute entrée où la condition de bug est fausse, le
correctif produit le même résultat que l'original (Property 2).

**Pseudocode :**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT app_original(input) = app_fixed(input)
END FOR
```

**Testing Approach** : le test basé sur les propriétés (Hypothesis) est
recommandé pour la préservation car :
- il génère de nombreux cas automatiquement sur le domaine d'entrée (ensemble des
  chemins SSR publics, échantillon d'endpoints `/api/v1/*`) ;
- il capture des cas limites que des tests unitaires manuels manqueraient ;
- il offre une garantie forte que le comportement est inchangé hors condition de
  bug.

**Test Plan** : observer d'abord la ligne de base sur le code NON corrigé (les
huit entrées et leur ordre, l'accès public sans auth, les réponses `auth`), puis
écrire des tests qui affirment cette ligne de base après correction. Réutiliser
les utilitaires existants de `test_navigation_and_styling_preservation.py`.

**Test Cases** :
1. **Préservation des huit entrées et de leur ordre** : observer sur le code non
   corrigé les huit `NAV_ITEMS` (labels + href, dans l'ordre), puis vérifier que
   ces huit entrées apparaissent inchangées et dans le même ordre après correction
   (la neuvième « Connexion » venant après).
2. **Accès public préservé** : observer que `/programme`, `/themes`,
   `/propositions`, `/statistiques`, `/equipes`, `/mandat`, `/assistant` et les
   pages RGPD répondent `200` en HTML sans authentification, puis l'affirmer
   après correction.
3. **Endpoints `auth` préservés** : observer statut/forme d'un échantillon de
   `/api/v1/auth/*` (ex. `login` avec identifiants invalides ⇒ `401` générique)
   sur le code non corrigé, puis affirmer l'identité après correction.
4. **Repli `nav_items` préservé** : vérifier que `base.html` rendu sans
   `nav_items` affiche toujours au moins « Programme ».

### Unit Tests

- Rendu de `/login` : statut `200`, type `text/html`, présence d'un formulaire
  ciblant `/api/v1/auth/login` avec champs email et mot de passe.
- Présence du lien « Connexion » (`/login`) dans le menu d'en-tête d'une page SSR.
- `NAV_ITEMS` : les huit premières entrées inchangées et dans l'ordre, la
  neuvième « Connexion » ajoutée en fin.
- Repli `base.html` : rendu sans `nav_items` → au moins le lien « Programme ».

### Property-Based Tests

- Pour tout chemin SSR public échantillonné, la page répond `200` en HTML sans
  authentification (préservation de l'accès public).
- Pour tout chemin SSR public échantillonné, le menu contient les huit entrées
  existantes dans l'ordre **et** l'entrée « Connexion » (résolvable vers `/login`).
- Pour tout endpoint `/api/v1/*` échantillonné (dont `auth`), statut et forme de
  réponse restent identiques à la ligne de base.

### Integration Tests

- Flux complet : depuis une page SSR, suivre le lien « Connexion » → `/login`
  répond `200` et rend le formulaire.
- Soumission du formulaire vers `POST /api/v1/auth/login` : identifiants valides
  ⇒ dépôt des cookies HttpOnly puis redirection vers une page publique ;
  identifiants invalides ⇒ `401` générique et message d'erreur générique affiché.
- Vérifier que la présence de l'entrée « Connexion » est cohérente sur plusieurs
  pages SSR (menu identique piloté par la source de vérité unique).
