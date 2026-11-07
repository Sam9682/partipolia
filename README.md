# PARTIPOLIA

*La reprise en main par les citoyens.* Plateforme de mobilisation économique citoyenne
et de mesure d'impact : les citoyens choisissent de réduire volontairement certaines
dépenses non essentielles, mesurent leurs économies et visualisent l'effet collectif
**estimé** sur les finances publiques, de façon transparente et non violente.

> Les montants affichés sont des **estimations modélisées**, pas des recettes fiscales
> mesurées. Les engagements sont **déclaratifs** (non vérifiés). Plusieurs paramètres
> sont encore **provisoires** : voir [`DATA_GAPS.md`](DATA_GAPS.md).

L'ancienne plateforme politique (propositions, programme, votes, « Réparer la loi »…) est
**archivée et masquée** : le code et les tables restent en place, mais ses routes ne sont
plus montées (voir [`ARCHITECTURE_ASSESSMENT.md`](ARCHITECTURE_ASSESSMENT.md)).

## Fonctionnalités (phases 0 à 3)

- Page d'accueil, navigation française, interface mobile d'abord (Tailwind compilé, aucun CDN).
- **Simulateur** en six catégories, sans inscription : économies, TVA, chaîne de
  fournisseurs, emploi, carburant, avec fourchettes basse / centrale / haute.
- **Moteur fiscal** indépendant du web (`app/economics`), en `Decimal`, déterministe :
  direct (TVA par taux, taxes carburant), indirect (modèle de Leontief, INSEE A38),
  induit (élasticités d'emploi), effet de rebond, impact net.
- **Données versionnées** : import CSV, validation, publication de versions de modèle
  immuables, comparaison, journal d'audit. Chaque résultat nomme sa version de modèle et
  de jeux de données ; les simulations enregistrées se recalculent à l'identique.
- **Comptes et engagements** déclaratifs (créer, modifier, suspendre, renouveler),
  consentement explicite à l'agrégation, export et suppression des données (RGPD).
- **Tableau de bord national** agrégé et anonyme, avec seuil minimal de participants.
- **Rôles** : super administrateur, administrateur de données, de contenu, modérateur, citoyen.

Hors périmètre de cette version : simulateur de scénarios nationaux, agrégation par région,
revendications interactives et consultations (phases 4 et 5), audit de sécurité et tests de
charge (phase 6).

## Stack

Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async), PostgreSQL 16 + pgvector, Redis,
Celery, Jinja2 / HTMX / Alpine.js / Tailwind (compilé), Chart.js (embarqué), Alembic,
Docker Compose. Monolithe modulaire.

```
app/
  economics/    moteur de simulation pur (sans FastAPI ni SQLAlchemy) + données CSV
  core/         configuration, base, Redis, journalisation, middleware
  models/       modèles SQLAlchemy
  schemas/      schémas Pydantic
  services/     services métier (simulation, engagements, agrégation, données)
  api/v1/       API REST /api/v1
  web/          pages SSR (publiques, administration)
  workers/      tâches Celery (rafraîchissement du tableau de bord)
  templates/    gabarits Jinja2 (public/ = Partipolia)
  static/       CSS compilé, JS embarqué
tests/          unit · economic · integration · api · security · rag
tools/          construction des jeux de données INSEE, compilation CSS
```

## Démarrage rapide

```bash
make install                # dépendances (uv)
cp .env.example .env        # renseigner les variables (aucun secret dans Git)
make up                     # docker compose (web, worker, postgres, redis, nginx)
make migrate                # migrations (aussi appliquées au démarrage du conteneur web)
make seed-model             # publie la version initiale du modèle (idempotent, aussi au démarrage)
python -m scripts.create_admin   # crée un super administrateur
```

## Développement

| Cible | Description |
|-------|-------------|
| `make test` | Suite `pytest` |
| `make lint` / `make type` | `ruff` / `mypy` |
| `make css` | Recompile la feuille Tailwind (binaire autonome `tools/tailwindcss`, non versionné) |
| `make seed-model` | Importe et publie les jeux de données livrés |

Les tests qui nécessitent PostgreSQL sont ignorés sans base : définissez
`TEST_DATABASE_URL=postgresql+psycopg://…` vers une base **jetable** (ses tables sont
recréées puis supprimées).

### Données économiques

Les CSV de `app/economics/data/` sont la seule source des paramètres initiaux.
`tools/build_insee_datasets.py` régénère les fichiers INSEE (dépendance de développement
`openpyxl`). Toute donnée sans source officielle vérifiée est livrée comme hypothèse
**provisoire**, avec une note et un niveau de confiance, et listée dans `DATA_GAPS.md`.
Un administrateur de données peut importer un nouveau jeu (`POST /api/v1/admin/economic-data/import`),
créer une version de modèle puis la publier ; une version publiée ne change plus jamais.

## Sécurité et vie privée

- Mots de passe hachés (Argon2), cookies de session HttpOnly et SameSite=Lax, corps JSON
  uniquement pour les écritures, CORS restreint, en-têtes de sécurité, limitation de débit.
- Aucune donnée de transaction ni bancaire ; engagements déclaratifs ; agrégats publiés
  uniquement au-delà d'un seuil minimal de participants et seulement pour les comptes
  ayant consenti.
- Export (`GET /api/v1/citizens/me/export`) et suppression de compte.
