"""Exécuteur des profils de charge (Exigences 16.2, 17.10).

Pour chaque profil : échantillonne ``docker stats`` (≤ 5 s), lance Locust en mode headless,
puis produit le rapport JSON (``loadtest.report``). Les erreurs HTTP n'influencent pas le code
de sortie de Locust (``--exit-code-on-error 0``) : seul un profil qui ne démarre pas (URL non
autorisée, Locust introuvable, aucune statistique produite) donne un code de sortie non nul.
Tous les profils sont tentés ; le code final est 1 si au moins un profil n'a pas démarré.

CLI : ``python -m loadtest.run [--profiles a,b] [--host URL] [--seed N] [--out DIR] [--check]``
``--check`` applique en plus ``loadtest.check_thresholds`` (profil fumée, code 1 si seuil dépassé).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from loadtest import check_thresholds, report
from loadtest.guard import TargetNotAllowedError, ensure_allowed

ALL_PROFILES = ("public_read", "compute", "auth_write", "participation_spike")
DEFAULT_HOST = "http://localhost:8080"
EXIT_OK = 0
EXIT_START_FAILED = 1


def _run_profile(profile: str, host: str, seed: int, out: Path, revision: str) -> int:
    csv_prefix = out / profile
    env = {**os.environ, "LOADTEST_PROFILE": profile, "LOADTEST_SEED": str(seed)}
    sampler = report.DockerStatsSampler()
    sampler.start()
    try:
        proc = subprocess.run(
            [
                sys.executable, "-m", "locust", "-f", "loadtest/locustfile.py", "--headless",
                "-H", host, "--csv", str(csv_prefix), "--exit-code-on-error", "0",
            ],
            env=env, check=False,
        )
    except OSError as exc:
        sampler.stop()
        print(f"ERREUR : profil {profile} non démarré : {exc}", file=sys.stderr)
        return EXIT_START_FAILED
    resources = sampler.stop()
    if proc.returncode != 0:
        print(f"ERREUR : profil {profile} non démarré ou interrompu (code {proc.returncode}).", file=sys.stderr)
        return EXIT_START_FAILED
    resources_path = out / f"{profile}_resources.json"
    resources_path.write_text(json.dumps(resources, indent=2) + "\n", encoding="utf-8")
    return report.main([
        "--profile", profile, "--csv", str(csv_prefix), "--output", str(out / f"{profile}.json"),
        "--resources", str(resources_path), "--revision", revision,
    ])


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--profiles", default=",".join(ALL_PROFILES))
    p.add_argument("--host", default=os.environ.get("LOADTEST_HOST", DEFAULT_HOST))
    p.add_argument("--seed", type=int, default=int(os.environ.get("LOADTEST_SEED", "1")))
    p.add_argument("--out", type=Path, default=Path("reports/loadtest"))
    p.add_argument("--revision", default=os.environ.get("GIT_REVISION", ""))
    p.add_argument("--check", action="store_true", help="vérifie les seuils (p95, taux d'erreur)")
    args = p.parse_args(argv)

    profiles = [x.strip() for x in args.profiles.split(",") if x.strip()]
    unknown = [x for x in profiles if x not in (*ALL_PROFILES, "smoke")]
    if unknown:
        print(f"ERREUR : profil inconnu : {', '.join(unknown)}", file=sys.stderr)
        return EXIT_START_FAILED
    try:
        ensure_allowed(args.host)
    except TargetNotAllowedError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return EXIT_START_FAILED

    args.out.mkdir(parents=True, exist_ok=True)
    code = EXIT_OK
    for profile in profiles:
        if _run_profile(profile, args.host, args.seed, args.out, args.revision) != 0:
            code = EXIT_START_FAILED
        elif args.check:
            code = max(code, check_thresholds.main([str(args.out / f"{profile}.json")]))
    return code


if __name__ == "__main__":
    sys.exit(main())
