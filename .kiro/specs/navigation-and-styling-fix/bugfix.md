# Bugfix Requirements Document

## Introduction

Sur le site déployé (https://partipolia.fr/programme), l'application PARTIPOLAI présente deux défauts visibles simultanément :

1. **Navigation incomplète** — L'en-tête n'affiche qu'un seul élément de menu (« Programme ») alors que l'outil expose de nombreuses fonctions (propositions, arguments, mandat, documents, thèmes, chat, statistiques, équipes, commentaires, sources, votes, etc.). Les utilisateurs ne peuvent donc pas accéder aux autres fonctions depuis la barre de navigation.

2. **Absence de styles/graphisme** — La page s'affiche comme un document HTML brut, non stylé (police serif par défaut, liens non stylés), et non comme une application web moderne. Le style est chargé exclusivement depuis des CDN externes (`https://cdn.tailwindcss.com`, `unpkg.com` pour HTMX et Alpine.js). Le répertoire `app/static` est vide (seul `.gitkeep`), il n'existe donc aucune feuille de style locale de repli. Lorsque le CDN Tailwind ne se charge pas dans l'environnement de production (blocage réseau, hors-ligne, ou CSP), la page perd tout son style.

Cette impact dégrade fortement l'utilisabilité : l'utilisateur ne trouve pas les fonctions et perçoit un site cassé.

**Contexte du défaut (référence, non normatif) :**
- `app/templates/base.html` : le `<nav>` de l'en-tête ne contient qu'un lien `/programme` ; le style est chargé uniquement via `cdn.tailwindcss.com` (aucune CSS locale).
- `app/web/router.py` : seules les pages SSR `/programme` et RGPD (`/mentions-legales`, `/confidentialite`, `/cookies`, `/conditions`) sont implémentées.
- `app/main.py` : `StaticFiles` est monté sur `/static` mais `app/static` est vide.

## Bug Analysis

### Current Behavior (Defect)

Ce qui se produit actuellement lorsque le bug est déclenché.

1.1 WHEN un utilisateur consulte n'importe quelle page rendue via `base.html` THEN le système n'affiche dans le menu de navigation de l'en-tête qu'un seul lien (« Programme »), aucun autre lien vers les fonctions principales de l'outil n'étant présent.

1.2 WHEN la page est rendue dans un environnement où `https://cdn.tailwindcss.com` n'est pas accessible (blocage réseau, hors-ligne ou CSP restrictive) THEN le système affiche une page HTML totalement non stylée (polices par défaut du navigateur, liens et éléments non mis en forme), aucune feuille de style locale de repli n'étant chargée.

1.3 WHEN le navigateur demande une ressource de style ou de script sous `/static` THEN le système ne sert aucun actif de style local (le répertoire `app/static` étant vide), le rendu dépendant donc entièrement des CDN externes.

### Expected Behavior (Correct)

Ce qui devrait se produire à la place.

2.1 WHEN un utilisateur consulte n'importe quelle page rendue via `base.html` THEN le système SHALL afficher dans le menu de navigation de l'en-tête des liens vers les fonctions principales de l'outil (au-delà du seul « Programme »), donnant accès aux autres fonctions disponibles.

2.2 WHEN la page est rendue dans un environnement où `https://cdn.tailwindcss.com` n'est pas accessible THEN le système SHALL rendre la page avec un style correct (mise en forme moderne et lisible) grâce à une solution de style fiable/locale, indépendamment de la disponibilité du CDN externe.

2.3 WHEN le navigateur demande la feuille de style de l'application sous `/static` THEN le système SHALL servir un actif de style local présent dans `app/static`, fournissant ainsi un repli fiable au rendu.

### Unchanged Behavior (Regression Prevention)

Comportements existants qui doivent être préservés.

3.1 WHEN un utilisateur consulte la page `/programme` THEN le système SHALL CONTINUE TO afficher le contenu du programme (thèmes, mesures, votes, agrégats financiers) sans authentification.

3.2 WHEN un utilisateur consulte les pages RGPD (`/mentions-legales`, `/confidentialite`, `/cookies`, `/conditions`) THEN le système SHALL CONTINUE TO les rendre sans authentification.

3.3 WHEN les liens de navigation du pied de page (Mentions légales, Confidentialité, Cookies, Conditions) sont utilisés THEN le système SHALL CONTINUE TO pointer vers leurs pages respectives.

3.4 WHEN `https://cdn.tailwindcss.com` EST accessible dans l'environnement THEN le système SHALL CONTINUE TO rendre les pages correctement (le repli local ne doit pas dégrader le rendu existant lorsque le CDN fonctionne).

3.5 WHEN les endpoints de l'API sous `/api/v1` sont appelés THEN le système SHALL CONTINUE TO répondre comme avant (le correctif ne concerne que la couche SSR / présentation).

## Bug Condition and Properties

### Bug Condition

Deux conditions de bug distinctes sont couvertes par ce correctif.

```pascal
FUNCTION isNavBugCondition(X)
  INPUT: X of type PageRenderRequest
  OUTPUT: boolean

  // Toute page rendue via base.html expose une navigation d'en-tête incomplète.
  RETURN X.rendersLayout = base_html
END FUNCTION
```

```pascal
FUNCTION isStylingBugCondition(X)
  INPUT: X of type PageRenderContext
  OUTPUT: boolean

  // Le rendu s'appuie exclusivement sur le CDN Tailwind, sans style local,
  // et le CDN est indisponible.
  RETURN X.rendersLayout = base_html
     AND X.tailwindCdnAvailable = false
     AND X.hasLocalStylesheet = false
END FUNCTION
```

### Property — Fix Checking

```pascal
// Propriété : Navigation complète
FOR ALL X WHERE isNavBugCondition(X) DO
  result ← renderPage'(X)
  ASSERT headerNav(result) CONTAINS links_to_main_functions
     AND headerNav(result).count > 1
END FOR
```

```pascal
// Propriété : Style fiable indépendant du CDN
FOR ALL X WHERE isStylingBugCondition(X) DO
  result ← renderPage'(X)
  ASSERT isStyled(result) = true            // rendu mis en forme
     AND servesLocalStylesheet('/static') = true
END FOR
```

### Preservation Goal

```pascal
// Propriété : Préservation du comportement existant
FOR ALL X WHERE NOT isNavBugCondition(X) AND NOT isStylingBugCondition(X) DO
  ASSERT renderPage(X) = renderPage'(X)
END FOR
```

Autrement dit : lorsque le CDN Tailwind est disponible et que la fonction ne relève pas des conditions de bug, la page corrigée (`F'`) doit se comporter de façon identique à la page d'origine (`F`).
