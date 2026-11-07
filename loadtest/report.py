"""Rapport de charge (Exigences 16.10, 17.9, 17.10, 18.5).

Construit un rapport JSON à partir des statistiques CSV de Locust
(``--csv <préfixe>`` produit ``<préfixe>_stats.csv``) :

* par point d'accès : débit, médiane, p95, p99, taux d'erreur ;
* échantillonnage ``docker stats`` (au plus 5 s) : moyenne et maximum du CPU et de la mémoire ;
* pour chaque objectif manqué : cible, mesure, cause identifiée et action corrective.

Les réponses 429 explicites ne sont pas des erreurs (17.1) : le locustfile doit les marquer
comme réussies ; le taux d'erreur ici est celui rapporté par Locust.

CLI : ``python -m loadtest.report --profile smoke --csv out/smoke --output out/smoke.json``
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
import threading
from datetime import date
from pathlib import Path
from typing import Any

# Objectifs p95 (ms) par profil (Exigences 17.1 à 17.3, 18.2) et taux d'erreur maximal.
PROFILE_OBJECTIVES: dict[str, dict[str, float]] = {
    "public_read": {"p95_ms": 500.0, "error_rate": 0.01},
    "compute": {"p95_ms": 1000.0},
    "auth_write": {"p95_ms": 800.0},
    "smoke": {"p95_ms": 1000.0, "error_rate": 0.01},
}

MAX_SAMPLE_INTERVAL_S = 5.0
AGGREGATED = "Aggregated"
UNKNOWN = "non identifiée"


def _num(value: str | None) -> float:
    try:
        return float(value) if value not in (None, "", "N/A") else 0.0
    except ValueError:
        return 0.0


def parse_locust_stats(csv_path: Path) -> list[dict[str, Any]]:
    """Lit ``*_stats.csv`` de Locust ; retourne une entrée par point d'accès (+ « Aggregated »)."""
    endpoints: list[dict[str, Any]] = []
    with csv_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            requests = int(_num(row.get("Request Count")))
            failures = int(_num(row.get("Failure Count")))
            name = row.get("Name") or ""
            method = row.get("Type") or ""
            endpoints.append(
                {
                    "name": name if name == AGGREGATED else f"{method} {name}".strip(),
                    "requests": requests,
                    "failures": failures,
                    "rps": round(_num(row.get("Requests/s")), 3),
                    "median_ms": _num(row.get("Median Response Time") or row.get("50%")),
                    "p95_ms": _num(row.get("95%")),
                    "p99_ms": _num(row.get("99%")),
                    "error_rate": (failures / requests) if requests else 0.0,
                }
            )
    return endpoints


# --------------------------------------------------------------------------- docker stats

# Facteurs de conversion vers des Mo (1 Mo = 1 000 000 octets).
_MEM_UNITS = {"b": 1e-6, "kb": 1e-3, "mb": 1.0, "gb": 1e3, "tb": 1e6,
              "kib": 1024 / 1e6, "mib": 1024**2 / 1e6, "gib": 1024**3 / 1e6, "tib": 1024**4 / 1e6}


def parse_docker_stats_line(line: str) -> tuple[str, float, float] | None:
    """Analyse une ligne JSON de ``docker stats --format '{{json .}}'`` : (service, cpu %, mémoire Mo)."""
    try:
        data = json.loads(line)
        name = str(data["Name"])
        cpu = float(str(data["CPUPerc"]).rstrip("%"))
        used = str(data["MemUsage"]).split("/")[0].strip()
    except (ValueError, KeyError, TypeError):
        return None
    m = re.fullmatch(r"([\d.]+)\s*([A-Za-z]+)", used)
    if not m or m.group(2).lower() not in _MEM_UNITS:
        return None
    return name, cpu, float(m.group(1)) * _MEM_UNITS[m.group(2).lower()]


class DockerStatsSampler:
    """Échantillonne ``docker stats`` toutes les ``interval`` secondes (≤ 5 s) dans un thread."""

    def __init__(self, interval: float = MAX_SAMPLE_INTERVAL_S, services: list[str] | None = None) -> None:
        self.interval = min(float(interval), MAX_SAMPLE_INTERVAL_S)
        self.services = services
        self.samples: dict[str, list[tuple[float, float]]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def sample_once(self) -> None:
        try:
            out = subprocess.run(
                ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
                capture_output=True, text=True, timeout=30, check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            return
        for line in out.splitlines():
            parsed = parse_docker_stats_line(line)
            if parsed and (not self.services or any(s in parsed[0] for s in self.services)):
                self.samples.setdefault(parsed[0], []).append((parsed[1], parsed[2]))

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample_once()
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> dict[str, dict[str, float]]:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=35)
        return summarize_resources(self.samples)


def summarize_resources(samples: dict[str, list[tuple[float, float]]]) -> dict[str, dict[str, float]]:
    """Moyenne et maximum du CPU (%) et de la mémoire (Mo) par service."""
    summary: dict[str, dict[str, float]] = {}
    for service, values in sorted(samples.items()):
        if not values:
            continue
        cpus = [v[0] for v in values]
        mems = [v[1] for v in values]
        summary[service] = {
            "samples": len(values),
            "cpu_avg_pct": round(statistics.fmean(cpus), 2),
            "cpu_max_pct": round(max(cpus), 2),
            "mem_avg_mb": round(statistics.fmean(mems), 2),
            "mem_max_mb": round(max(mems), 2),
        }
    return summary


# --------------------------------------------------------------------------- objectifs

def evaluate_objectives(
    profile: str,
    endpoints: list[dict[str, Any]],
    analysis: dict[str, dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Retourne, pour chaque objectif manqué : cible, mesure, cause et action corrective (17.9).

    ``analysis`` associe un point d'accès (ou ``*``) à ``{"cause": ..., "action": ...}`` ;
    à défaut, la cause est « non identifiée » et l'action « à définir ».
    """
    targets = PROFILE_OBJECTIVES.get(profile, {})
    analysis = analysis or {}
    misses: list[dict[str, Any]] = []
    for ep in endpoints:
        info = analysis.get(ep["name"]) or analysis.get("*") or {}
        checks = (
            ("p95_ms", "p95", "ms", ep["p95_ms"] > targets.get("p95_ms", float("inf"))),
            ("error_rate", "taux d'erreur", "", ep["error_rate"] >= targets.get("error_rate", float("inf"))),
        )
        for key, label, unit, missed in checks:
            if missed:
                misses.append(
                    {
                        "endpoint": ep["name"],
                        "objective": label,
                        "target": targets[key],
                        "measured": ep["p95_ms"] if key == "p95_ms" else ep["error_rate"],
                        "unit": unit,
                        "cause": info.get("cause", UNKNOWN),
                        "action": info.get("action", "à définir"),
                    }
                )
    return misses


def build_report(
    profile: str,
    endpoints: list[dict[str, Any]],
    resources: dict[str, dict[str, float]] | None = None,
    *,
    revision: str = "",
    model_version: str = "",
    analysis: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "profile": profile,
        "date": date.today().isoformat(),
        "revision": revision,
        "model_version": model_version,
        "endpoints": endpoints,
        "resources": resources or {},
        "missed_objectives": evaluate_objectives(profile, endpoints, analysis),
    }


def render_text(report: dict[str, Any]) -> str:
    """Tableau lisible du rapport (débit, médiane, p95, p99, erreurs, ressources, objectifs manqués)."""
    lines = [
        f"Rapport de charge — profil {report['profile']} ({report['date']})",
        f"{'Point d accès':<48}{'req/s':>8}{'méd.':>8}{'p95':>8}{'p99':>8}{'err %':>8}",
    ]
    for ep in report["endpoints"]:
        lines.append(
            f"{ep['name'][:47]:<48}{ep['rps']:>8.2f}{ep['median_ms']:>8.0f}"
            f"{ep['p95_ms']:>8.0f}{ep['p99_ms']:>8.0f}{ep['error_rate'] * 100:>8.2f}"
        )
    if report["resources"]:
        lines.append("\nRessources (moyenne / maximum)")
        for svc, r in report["resources"].items():
            lines.append(
                f"  {svc}: CPU {r['cpu_avg_pct']:.1f}% / {r['cpu_max_pct']:.1f}%, "
                f"mémoire {r['mem_avg_mb']:.0f} / {r['mem_max_mb']:.0f} Mo"
            )
    for m in report["missed_objectives"]:
        lines.append(
            f"\nObjectif manqué — {m['endpoint']} : cible {m['objective']} {m['target']}{m['unit']}, "
            f"mesure {m['measured']}{m['unit']}, cause : {m['cause']}, action : {m['action']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--profile", required=True, choices=sorted(PROFILE_OBJECTIVES))
    p.add_argument("--csv", required=True, help="préfixe CSV Locust (lit <préfixe>_stats.csv)")
    p.add_argument("--output", required=True, help="fichier JSON de sortie")
    p.add_argument("--resources", help="JSON de ressources produit par DockerStatsSampler")
    p.add_argument("--analysis", help="JSON {point d'accès: {cause, action}} pour les objectifs manqués")
    p.add_argument("--revision", default="")
    p.add_argument("--model-version", default="")
    args = p.parse_args(argv)

    stats = Path(f"{args.csv}_stats.csv")
    if not stats.is_file():
        print(f"ERREUR : statistiques introuvables : {stats}", file=sys.stderr)
        return 2
    resources = json.loads(Path(args.resources).read_text("utf-8")) if args.resources else None
    analysis = json.loads(Path(args.analysis).read_text("utf-8")) if args.analysis else None
    report = build_report(
        args.profile, parse_locust_stats(stats), resources,
        revision=args.revision, model_version=args.model_version, analysis=analysis,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
