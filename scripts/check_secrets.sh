#!/usr/bin/env bash
# Détection de secrets (detect-secrets) sur les fichiers suivis par git.
# Usage : scripts/check_secrets.sh [--update-baseline]
# Échoue (code 1) si un secret absent de .secrets.baseline est détecté.
set -euo pipefail

cd "$(dirname "$0")/.."
BASELINE=".secrets.baseline"
EXCLUDE='^(uv\.lock|\.secrets\.baseline)$'

if [ "${1:-}" = "--update-baseline" ]; then
  git ls-files -z | xargs -0 uv run detect-secrets scan --exclude-files "$EXCLUDE" > "$BASELINE"
  echo "Baseline mise à jour : $BASELINE"
  exit 0
fi

mkdir -p reports/security
git ls-files -z | xargs -0 uv run detect-secrets scan --exclude-files "$EXCLUDE" \
  > reports/security/secrets-scan.json

uv run python - "$BASELINE" reports/security/secrets-scan.json <<'PY'
import json
import sys

def keys(path):
    data = json.load(open(path, encoding="utf-8"))
    return {
        (f, s["hashed_secret"])
        for f, items in data["results"].items()
        for s in items
    }

new = keys(sys.argv[2]) - keys(sys.argv[1])
if new:
    for f, _ in sorted(new):
        print(f"Secret potentiel détecté : {f}", file=sys.stderr)
    sys.exit(1)
print("Aucun nouveau secret détecté.")
PY
