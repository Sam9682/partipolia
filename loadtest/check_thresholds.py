"""Vérificateur de seuils du profil « fumée » (Exigences 18.2, 18.3, 18.7).

Échoue (code 1) si, pour un point d'accès, p95 > 1000 ms ou taux d'erreur ≥ 1 %.
Le message indique le point d'accès, la valeur mesurée et le seuil. Affiche l'écart du p95
et du taux d'erreur par rapport à ``docs/perf/reference.json`` s'il existe.

CLI : ``python -m loadtest.check_thresholds <rapport.json> [--reference docs/perf/reference.json]``
Codes de sortie : 0 conforme, 1 seuil dépassé, 2 rapport illisible ou vide.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

P95_THRESHOLD_MS = 1000.0
ERROR_RATE_THRESHOLD = 0.01
DEFAULT_REFERENCE = Path(__file__).resolve().parent.parent / "docs" / "perf" / "reference.json"


def check(
    report: dict[str, Any],
    p95_threshold_ms: float = P95_THRESHOLD_MS,
    error_threshold: float = ERROR_RATE_THRESHOLD,
) -> list[str]:
    """Liste des violations ; vide si le rapport est conforme (échec ssi p95 > seuil ou erreurs ≥ seuil)."""
    violations: list[str] = []
    for ep in report.get("endpoints", []):
        name = ep["name"]
        if ep["p95_ms"] > p95_threshold_ms:
            violations.append(
                f"{name} : p95 mesuré {ep['p95_ms']:.0f} ms > seuil {p95_threshold_ms:.0f} ms"
            )
        if ep["error_rate"] >= error_threshold:
            violations.append(
                f"{name} : taux d'erreur mesuré {ep['error_rate'] * 100:.2f} % "
                f">= seuil {error_threshold * 100:.2f} %"
            )
    return violations


def compare_with_reference(report: dict[str, Any], reference: dict[str, Any]) -> list[str]:
    """Écart du p95 (ms) et du taux d'erreur (points de %) par point d'accès présent dans les deux rapports."""
    ref = {e["name"]: e for e in reference.get("endpoints", [])}
    lines: list[str] = []
    for ep in report.get("endpoints", []):
        base = ref.get(ep["name"])
        if base is None:
            lines.append(f"{ep['name']} : absent de la référence")
            continue
        dp95 = ep["p95_ms"] - base["p95_ms"]
        derr = (ep["error_rate"] - base["error_rate"]) * 100
        lines.append(f"{ep['name']} : écart p95 {dp95:+.0f} ms, écart erreurs {derr:+.2f} pt")
    return lines


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("report", type=Path)
    p.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    args = p.parse_args(argv)

    try:
        report = json.loads(args.report.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERREUR : rapport illisible ({exc})", file=sys.stderr)
        return 2
    if not report.get("endpoints"):
        print("ERREUR : rapport sans point d'accès", file=sys.stderr)
        return 2

    if args.reference.is_file():
        try:
            reference = json.loads(args.reference.read_text("utf-8"))
            print("Écart avec la référence :")
            print("\n".join(f"  {line}" for line in compare_with_reference(report, reference)))
        except (OSError, ValueError, KeyError) as exc:
            print(f"Référence inexploitable : {exc}", file=sys.stderr)
    else:
        print(f"Aucune référence ({args.reference}) : écart non affiché.")

    violations = check(report)
    for v in violations:
        print(f"ÉCHEC : {v}", file=sys.stderr)
    if violations:
        return 1
    print("Seuils respectés.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
