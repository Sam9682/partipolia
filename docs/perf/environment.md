# Environnement de référence des tests de charge

Cet environnement sert de base aux mesures de `make loadtest`, `make loadtest-smoke` et aux
rapports de `docs/perf/` (Exigences 16.3, 18.10). Les limites sont définies dans
`docker-compose.loadtest.yml`.

## Ressources allouées par service

| Service    | CPU (limite) | Mémoire (limite) | Version / image                    |
|------------|--------------|------------------|------------------------------------|
| web        | 2,0          | 2 Go             | Python 3.13 (`Dockerfile`)         |
| worker     | 1,0          | 1 Go             | Python 3.13 (`Dockerfile.worker`)  |
| postgres   | 2,0          | 2 Go             | `pgvector/pgvector:pg16`           |
| redis      | 0,5          | 512 Mo           | `redis:7-alpine`                   |
| nginx      | 0,5          | 256 Mo           | `nginxinc/nginx-unprivileged:1.27-alpine` (profil `edge`, non démarré par défaut) |

Outil de charge : Locust 2.32.5 (épinglé dans `pyproject.toml`). Le service `web` est publié
directement sur `http://localhost:8080`, origine autorisée par `loadtest/config.toml`.

## Jeu de données de départ

1. Migrations : `make migrate`.
2. Données historiques (thèmes, programme, administrateur) : `make seed`.
3. Version initiale du modèle économique : `make seed-model`.
4. Comptes et objets de test : générés par `loadtest/data.py` à partir de `LOADTEST_SEED`
   (défaut `1`), tous préfixés `loadtest+<graine>-` (domaine e-mail `loadtest.example.com`) et
   supprimés en fin d'exécution.

## Commande de démarrage

```bash
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml up -d --build
make migrate seed seed-model
make loadtest-smoke        # profil fumée (20 VU, 60 s)
make loadtest              # 4 profils : public_read, compute, auth_write, participation_spike
```

Les rapports (CSV Locust, JSON par profil, ressources `docker stats` moyenne / maximum) sont
écrits dans `reports/loadtest/`. `make loadtest` retourne un code non nul si un profil ne démarre
pas. Les tests de performance d'agrégation (`make perf-test`) s'exécutent hors conteneur.

## Arrêt

```bash
docker compose -f docker-compose.yml -f docker-compose.loadtest.yml down
```
