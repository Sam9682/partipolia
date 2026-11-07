#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# PARTIPOLAI — Point d'entrée du service Web.
#
# Applique les migrations Alembic (`upgrade head`) avant de lancer le serveur
# ASGI. `alembic upgrade head` est idempotent : sans migration en attente, il
# ne fait rien. Cela évite les erreurs « relation "…" does not exist » lorsque
# le schéma n'a pas encore été appliqué à la base (Exigence 32.4).
# ---------------------------------------------------------------------------
set -euo pipefail

echo "[entrypoint] Application des migrations Alembic (upgrade head)…"
alembic upgrade head

# Partipolia : publie la version initiale du modèle économique (idempotent). Un
# échec n'empêche pas le démarrage : le simulateur retombe alors sur le jeu de
# données embarqué et indique la version de modèle réellement utilisée.
echo "[entrypoint] Publication du modèle économique initial (si absent)…"
python -m scripts.seed_economic_model || echo "[entrypoint] AVERTISSEMENT : seed du modèle économique non appliqué"

echo "[entrypoint] Démarrage du serveur : $*"
exec "$@"
