# Navigation and Styling Fix — Bugfix Design

## Overview

Deux défauts affectent simultanément la couche de présentation (SSR) du site
PARTIPOLAI, tous deux ancrés dans `app/templates/base.html` :

1. **Navigation incomplète** — l'en-tête du gabarit racine code en dur un seul
   lien (`/programme`). Aucune entrée ne mène aux autres fonctions de l'outil.
2. **Absence de style hors-CDN** — le style, HTMX et Alpine.js sont chargés
   exclusivement depuis des CDN externes (`cdn.tailwindcss.com`, `unpkg.com`).
   `app/static` est vide (`.gitkeep`), il n'existe donc aucun repli local ;
   quand le CDN Tailwind ne se charge pas (blocage réseau, hors-ligne, CSP), la
   page s'affiche non stylée.

**Stratégie de correction (scope strict : couche SSR/présentation).**

- *Navigation* — Remplacer le lien unique par un menu construit à partir d'une
  **liste d'items de navigation déclarée côté serveur**, injectée dans le
  contexte de tous les gabarits SSR. Pour respecter la contrainte « pas de liens
  morts », chaque item n'est ajouté au menu **que s'il pointe vers une route SSR
  réellement résolvable**. À ce jour, seule `/programme` existe (hors RGPD, déjà
  dans le pied de page). Les autres fonctions n'ont que des endpoints
  `/api/v1/*`. La correction introduit donc, de façon minimale, un petit
  ensemble de pages SSR « index/liste » adossées aux services existants pour les
  fonctions déjà consultables publiquement (Thèmes, Propositions, Statistiques,
  Équipes, Mandat), plus une entrée « Assistant » et un point d'accès unique
  vers la documentation d'API pour les fonctions restant purement API. Le menu
  est ainsi complet (`count > 1`) **et** sans lien mort.

- *Style* — Ajouter une **feuille de style CSS locale compilée** servie depuis
  `/static` (répertoire `app/static`, déjà monté par `StaticFiles` dans
  `app/main.py`) et la référencer via `<link rel="stylesheet">` dans `base.html`.
  Le CDN Tailwind reste chargé quand il est disponible (Exigence 3.4) : le CSS
  local est un actif autonome qui garantit un rendu correct lorsque le CDN est
  absent, sans dégrader le rendu quand le CDN fonctionne.

Aucun endpoint `/api/v1/*` n'est modifié (Exigence 3.5).

## Glossary

- **Bug_Condition (C)** : condition déclenchant un bug. Deux conditions
  distinctes : `isNavBugCondition` (page rendue via `base.html`, navigation
  d'en-tête incomplète) et `isStylingBugCondition` (page via `base.html`, CDN
  Tailwind indisponible et aucune CSS locale).
- **Property (P)** : comportement attendu — l'en-tête expose des liens vers les
  fonctions principales (`count > 1`, sans lien mort) ; la page reste stylée et
  une feuille de style locale est servie sous `/static`.
- **Preservation** : comportement existant à ne pas modifier — rendu de
  `/programme` et des pages RGPD sans authentification, liens du pied de page,
  rendu identique quand le CDN Tailwind est disponible, endpoints `/api/v1/*`.
- **base.html** : gabarit racine Jinja2 (`app/templates/base.html`) dont héritent
  toutes les pages SSR ; contient l'en-tête (`<nav>`), le pied de page et le
  chargement des actifs (styles/scripts).
- **web_router** : `APIRouter` de `app/web/router.py` montant les pages SSR à la
  racine du site (Jinja2/HTMX). Actuellement : `/programme` et pages RGPD.
- **StaticFiles /static** : montage `app.mount("/static", StaticFiles(...))` dans
  `app/main.py`, servant `app/static` (aujourd'hui vide).
- **nav_items** : structure de navigation (liste d'objets `{label, href}`)
  injectée dans le contexte des gabarits SSR pour construire le menu d'en-tête.

## Bug Details

### Bug Condition

Le correctif couvre **deux conditions de bug distinctes**.

**Navigation** — le bug se manifeste sur toute page rendue via `base.html` :
l'en-tête ne rend qu'un lien (`/programme`), car le `<nav>` code en dur ce lien
unique au lieu d'itérer sur un ensemble d'items de navigation.

**Formal Specification :**
```
FUNCTION isNavBugCondition(X)
  INPUT: X of type PageRenderRequest
  OUTPUT: boolean

  RETURN X.rendersLayout = base_html
END FUNCTION
```

**Style** — le bug se manifeste quand une page via `base.html` est rendue alors
que le CDN Tailwind est indisponible et qu'aucune feuille de style locale n'est
disponible : le rendu dépend entièrement du CDN, la page apparaît non stylée.

**Formal Specification :**
```
FUNCTION isStylingBugCondition(X)
  INPUT: X of type PageRenderContext
  OUTPUT: boolean

  RETURN X.rendersLayout = base_html
     AND X.tailwindCdnAvailable = false
     AND X.hasLocalStylesheet = false
END FUNCTION
```

### Examples

- **Navigation** — Attendu : l'en-tête propose plusieurs liens (Programme,
  Propositions, Thèmes, Statistiques, Équipes, Mandat, Assistant…). Constaté :
  un seul lien « Programme » dans `<nav class="flex gap-4 text-sm">`.
- **Style, CDN bloqué** — Attendu : page mise en forme (mise en page, cartes,
  tableaux lisibles). Constaté : document HTML brut (police serif par défaut,
  liens bleus soulignés, aucune mise en page), car `cdn.tailwindcss.com` n'a pas
  chargé et aucun CSS local n'existe.
- **Requête `/static/...`** — Attendu : la feuille de style locale de
  l'application est servie (`200`). Constaté : aucun actif de style local
  (`app/static` ne contient que `.gitkeep`).
- **Cas limite — CDN disponible** — Attendu (préservation) : rendu identique à
  l'actuel. La CSS locale ne doit pas entrer en conflit ni dégrader le rendu
  Tailwind existant.

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors :**
- La page `/programme` continue d'afficher le contenu du Programme (thèmes,
  mesures, votes, agrégats financiers) sans authentification (Exigence 3.1).
- Les pages RGPD (`/mentions-legales`, `/confidentialite`, `/cookies`,
  `/conditions`) continuent d'être rendues sans authentification (Exigence 3.2).
- Les liens du pied de page (Mentions légales, Confidentialité, Cookies,
  Conditions) continuent de pointer vers leurs pages respectives (Exigence 3.3).
- Quand le CDN Tailwind est accessible, le rendu reste correct et inchangé ; le
  repli local ne dégrade pas le rendu existant (Exigence 3.4).
- Les endpoints `/api/v1/*` continuent de répondre à l'identique (Exigence 3.5).

**Scope :**
Tout ce qui ne relève pas des conditions de bug doit être totalement inchangé.
Cela inclut :
- le comportement de rendu des pages SSR existantes lorsque le CDN fonctionne ;
- l'intégralité de la surface `/api/v1/*` (aucun fichier `app/api/v1/*` modifié) ;
- les liens et le contenu du pied de page ;
- l'absence d'authentification pour les pages publiques.

> Le comportement correct attendu (côté correction) est défini dans la section
> **Correctness Properties** (Property 1) ; cette section-ci fixe ce qui ne doit
> **pas** changer.

## Hypothesized Root Cause

D'après l'analyse du bug et la lecture du code, les causes sont :

1. **Navigation codée en dur dans `base.html`** : le bloc
   `<nav class="flex gap-4 text-sm"><a href="/programme">Programme</a></nav>`
   ne contient qu'une entrée statique. Il n'existe aucune source de vérité pour
   la liste des liens de navigation, ni de boucle Jinja2 pour les rendre.

2. **Absence de pages SSR pour les autres fonctions** : `app/web/router.py`
   n'expose que `/programme` et les pages RGPD. Les fonctions proposals,
   arguments, mandate, documents, themes, chat, statistics, teams, comments,
   sources, votes, privacy n'existent que sous `/api/v1/*`. Ajouter des liens
   d'en-tête « bruts » créerait des liens morts (404). La liste de navigation
   doit donc n'inclure que des cibles réellement résolvables.

3. **Style dépendant uniquement du CDN** : `base.html` charge
   `https://cdn.tailwindcss.com` (et HTMX/Alpine via `unpkg.com`) sans aucune
   feuille de style locale. Il n'y a pas de `<link rel="stylesheet">` vers un
   actif `/static`.

4. **`app/static` vide** : bien que `StaticFiles` soit monté sur `/static` dans
   `app/main.py`, le répertoire ne contient que `.gitkeep`. Aucun actif de style
   n'est disponible en repli, donc le rendu échoue dès que le CDN ne charge pas.

## Correctness Properties

Property 1: Bug Condition — Navigation complète et style local fiable

_For any_ page rendue via `base.html` (isNavBugCondition vraie), la fonction de
rendu corrigée SHALL produire un en-tête dont le menu de navigation contient
plusieurs liens (`count > 1`) vers les fonctions principales de l'outil, chacun
pointant vers une route réellement résolvable (aucun lien mort). _For any_ page
rendue via `base.html` avec CDN Tailwind indisponible (isStylingBugCondition
vraie), la fonction de rendu corrigée SHALL produire une page mise en forme
grâce à une feuille de style locale présente et servie sous `/static`.

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation — Comportement inchangé hors conditions de bug

_For any_ requête ne relevant pas des conditions de bug (CDN Tailwind disponible
et hors périmètre navigation/style), le code corrigé SHALL produire exactement
le même résultat que le code d'origine : rendu inchangé de `/programme` et des
pages RGPD sans authentification, liens du pied de page inchangés, et réponses
identiques des endpoints `/api/v1/*`.

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

En supposant l'analyse de cause racine correcte, les changements restent
confinés à la couche SSR/présentation.

**Fichier** : `app/templates/base.html`

**Changements** :
1. **Menu de navigation piloté par les données** : remplacer le lien unique par
   une boucle Jinja2 sur `nav_items` (liste `{label, href}`), avec un repli sûr
   si `nav_items` est absent (au minimum le lien « Programme ») pour ne jamais
   régresser en dessous du comportement actuel.
2. **Feuille de style locale** : ajouter
   `<link rel="stylesheet" href="/static/css/app.css">` dans le `<head>`. Elle
   est chargée en plus du CDN. Placée de manière à fournir un rendu correct
   lorsque le CDN est absent, sans surcharger/casser le rendu Tailwind quand il
   est présent (le CDN reste la source principale ; le CSS local couvre la mise
   en page de base et les composants clés de `base.html`/`programme.html`).

**Fichier** : `app/web/router.py`

**Changements** :
3. **Source de vérité de la navigation** : définir la liste `nav_items` (ordre
   et libellés) et l'injecter dans le contexte de chaque `TemplateResponse` SSR.
   Pour éviter la duplication, factoriser via un contexte partagé (fonction
   utilitaire ou `context_processor` équivalent) appliqué aux vues SSR.
   `nav_items` ne contient **que des routes résolvables** :
   - `/programme` (existant),
   - pages « index/liste » nouvellement ajoutées pour les fonctions publiques
     déjà couvertes par des services (Thèmes, Propositions, Statistiques,
     Équipes, Mandat),
   - « Assistant » (chat) et « API » (documentation OpenAPI `/docs`) comme
     points d'accès pour les fonctions restant purement API, afin qu'aucune
     entrée ne mène à un 404.
4. **Nouvelles vues SSR minimales** (lecture seule, publiques, sans
   authentification, adossées aux services existants — `ThemeService`,
   `ProposalService`/`ProgramService`, `StatisticsService`, `TeamService`,
   mandat) : chaque vue rend un gabarit `templates/{fonction}.html` héritant de
   `base.html`. Le périmètre est volontairement restreint à des pages de liste
   suffisantes pour que le lien de nav soit résolvable et utile.

**Fichier** : `app/static/css/app.css` (nouveau)

**Changements** :
5. **CSS local compilé** : produire une feuille de style locale autonome
   couvrant la mise en page et les composants utilisés par `base.html` et les
   gabarits SSR (conteneur, en-tête/pied, cartes, tableaux, boutons/liens,
   typographie). Générée via un build Tailwind (CLI) restreint aux classes
   réellement utilisées par les gabarits, ou fournie comme CSS statique
   équivalent. L'actif compilé est commité dans `app/static/css/` afin d'être
   servi sans dépendance réseau au runtime.

**Fichiers** : `app/templates/{fonction}.html` (nouveaux)

**Changements** :
6. **Gabarits de liste** pour les nouvelles vues SSR, héritant de `base.html`,
   réutilisant les classes déjà présentes pour rester cohérents avec
   `programme.html`.

> Aucun fichier sous `app/api/v1/` n'est modifié. `app/main.py` n'a pas besoin
> de changer : `StaticFiles` sur `/static` sert déjà `app/static`.

## Testing Strategy

### Validation Approach

Approche en deux temps : d'abord faire apparaître des contre-exemples
démontrant chaque bug sur le code non corrigé, puis vérifier que la correction
fonctionne (Fix-Checking) et préserve le comportement existant
(Preservation-Checking). Les tests portent sur la couche SSR via le client de
test FastAPI/Starlette (`TestClient`/`httpx`) et l'inspection du HTML rendu.

### Exploratory Bug Condition Checking

**Goal** : faire apparaître des contre-exemples démontrant les bugs AVANT la
correction, et confirmer ou réfuter l'analyse de cause racine. En cas de
réfutation, ré-hypothétiser.

**Test Plan** : rendre les pages SSR via le client de test et inspecter le HTML
de l'en-tête et le chargement des styles ; simuler l'absence de CSS local en
inspectant `app/static`. Exécuter sur le code NON corrigé pour observer les
échecs.

**Test Cases** :
1. **Nav unique** : `GET /programme` puis compter les liens du `<nav>` d'en-tête
   → attendu ≤ 1 sur le code non corrigé (échoue vs la cible `> 1`).
2. **Aucune CSS locale** : `GET /static/css/app.css` → `404` sur le code non
   corrigé (démontre l'absence de repli local).
3. **Style uniquement CDN** : inspecter le `<head>` de `base.html` → absence de
   tout `<link rel="stylesheet">` local (dépendance exclusive au CDN).
4. **Cas limite — liens morts potentiels** : vérifier qu'aucune route SSR
   n'existe pour les fonctions autres que `/programme`/RGPD (justifie l'ajout de
   pages index plutôt que de liens bruts).

**Expected Counterexamples** :
- Le `<nav>` d'en-tête ne contient qu'un lien.
- `/static/css/app.css` renvoie `404` ; aucune feuille locale servie.
- Cause probable : navigation codée en dur + `app/static` vide + `<head>` sans
  `<link>` local.

### Fix Checking

**Goal** : vérifier que pour toute entrée relevant d'une condition de bug, la
fonction corrigée produit le comportement attendu (Property 1).

**Pseudocode :**
```
FOR ALL X WHERE isNavBugCondition(X) DO
  result := renderPage_fixed(X)
  ASSERT headerNav(result).count > 1
     AND headerNav(result) CONTAINS links_to_main_functions
     AND ALL link IN headerNav(result): resolves(link) = true   // pas de lien mort
END FOR

FOR ALL X WHERE isStylingBugCondition(X) DO
  result := renderPage_fixed(X)
  ASSERT isStyled(result) = true
     AND servesLocalStylesheet('/static') = true
END FOR
```

### Preservation Checking

**Goal** : vérifier que pour toute entrée ne relevant pas des conditions de bug,
la fonction corrigée produit le même résultat que la fonction d'origine
(Property 2).

**Pseudocode :**
```
FOR ALL X WHERE NOT isNavBugCondition(X) AND NOT isStylingBugCondition(X) DO
  ASSERT renderPage_original(X) = renderPage_fixed(X)
END FOR
```

**Testing Approach** : le test basé sur les propriétés est recommandé pour la
préservation — il génère automatiquement de nombreux cas sur le domaine
d'entrée (chemins SSR publics, endpoints `/api/v1/*` échantillonnés, présence
du CDN), couvre des cas limites que des tests unitaires manuels rateraient, et
apporte de fortes garanties que le comportement est inchangé pour les entrées
non buggy.

**Test Plan** : observer d'abord le comportement du code NON corrigé pour
`/programme`, les pages RGPD, le pied de page et un échantillon d'endpoints
`/api/v1/*`, puis écrire des tests capturant ce comportement pour vérifier qu'il
persiste après correction.

**Test Cases** :
1. **Préservation `/programme`** : le contenu du Programme (totaux, thèmes,
   mesures, agrégats) reste rendu sans authentification, identique à l'existant.
2. **Préservation pages RGPD** : `/mentions-legales`, `/confidentialite`,
   `/cookies`, `/conditions` restent rendues sans authentification.
3. **Préservation pied de page** : les quatre liens RGPD du pied de page
   pointent toujours vers leurs pages respectives.
4. **Préservation API** : un échantillon d'endpoints `/api/v1/*` renvoie des
   réponses identiques (statut/forme) avant et après la correction.
5. **Préservation CDN présent** : quand `cdn.tailwindcss.com` est référencé,
   le `<head>` conserve le chargement CDN existant (le CSS local s'ajoute sans
   le remplacer ni le casser).

### Unit Tests

- Rendu de `base.html` : le `<nav>` d'en-tête contient plusieurs liens issus de
  `nav_items` ; repli sûr si `nav_items` absent.
- Présence de `<link rel="stylesheet" href="/static/css/app.css">` dans le
  `<head>` et service `200` de cet actif via `/static`.
- Chaque nouvelle vue SSR de liste répond `200` sans authentification et hérite
  de `base.html`.
- Cas limites : `nav_items` vide, libellés/hrefs bien échappés (autoescape).

### Property-Based Tests

- Pour tout chemin de la liste `nav_items`, le rendu de l'en-tête contient un
  lien vers ce chemin et ce chemin est résolvable (aucun 404).
- Pour un ensemble généré de chemins SSR publics, le HTML rendu inclut toujours
  la feuille de style locale et un en-tête à `count > 1`.
- Pour un échantillon généré d'endpoints `/api/v1/*`, les réponses restent
  identiques avant/après (préservation).

### Integration Tests

- Parcours complet : depuis n'importe quelle page SSR, chaque lien d'en-tête
  mène à une page qui répond `200` (navigation de bout en bout, aucun lien mort).
- Rendu hors-ligne simulé (CDN indisponible) : la feuille de style locale est
  servie et la page reste mise en forme.
- Rendu avec CDN disponible : le comportement de rendu reste conforme à
  l'existant (non-régression visuelle de base).
