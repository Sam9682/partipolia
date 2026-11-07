"""Relevé des en-têtes de sécurité renvoyés derrière nginx (Exigences 13.1-13.4, 15.11).

Interroge la pile déployée (nginx -> web) pour trois réponses :

* la page d'accueil (``/``) ;
* une page authentifiée (``--auth-path``, ``/admin`` par défaut) ;
* une réponse d'API (``--api-path``, ``/api/v1/auth/me`` par défaut, authentifiée).

et écrit la section « Relevé des en-têtes de sécurité » de
``docs/security/audit-<date>.md`` (nom et valeur exacts de chaque en-tête). Seule
la section délimitée par les marqueurs ``<!-- BEGIN:security-headers -->`` /
``<!-- END:security-headers -->`` est remplacée ; le reste du rapport est conservé.
Si le rapport n'existe pas, il est créé à partir du gabarit (10 domaines, Constats,
Dérogations).

Authentification : ``--email`` / ``--password`` (ou variables d'environnement
``AUDIT_EMAIL`` / ``AUDIT_PASSWORD``) pour ``POST /api/v1/auth/login``. Les valeurs
des cookies (``Set-Cookie``) sont masquées : seuls leurs attributs sont relevés.
Sans identifiants, les réponses authentifiées sont consignées « non relevé ».

Exemple ::

    python tools/security_headers_report.py --base-url https://localhost \\
        --insecure --email audit@example.org --password '...'

Dépendances : bibliothèque standard uniquement.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import ssl
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

BEGIN_MARKER = "<!-- BEGIN:security-headers -->"
END_MARKER = "<!-- END:security-headers -->"

# En-têtes de sécurité relevés (comparaison insensible à la casse), dans l'ordre
# d'affichage. ``Set-Cookie`` est traité à part (valeur masquée).
SECURITY_HEADERS: tuple[str, ...] = (
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Content-Type-Options",
    "X-Frame-Options",
    "Referrer-Policy",
    "Permissions-Policy",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Resource-Policy",
    "Cross-Origin-Embedder-Policy",
    "X-XSS-Protection",
    "Cache-Control",
    "X-Request-ID",
    "Access-Control-Allow-Origin",
    "Access-Control-Allow-Credentials",
    "Retry-After",
    "Server",
    "X-Powered-By",
)

DOMAINS: tuple[str, ...] = (
    "Authentification et sessions",
    "Autorisation par rôle",
    "Validation des entrées",
    "Protection CSRF",
    "En-têtes de sécurité",
    "Limitation de débit",
    "Gestion des secrets",
    "Dépendances",
    "Configuration nginx et Docker",
    "Protection des données personnelles",
)

_COOKIE_VALUE = re.compile(r"^([^=;]+)=([^;]*)")


@dataclass
class Capture:
    """Résultat du relevé d'une réponse."""

    label: str
    method: str
    path: str
    status: int | None = None
    headers: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""


def redact_cookie(value: str) -> str:
    """Masque la valeur d'un ``Set-Cookie`` en conservant nom et attributs."""
    return _COOKIE_VALUE.sub(lambda m: f"{m.group(1)}=<masqué>", value, count=1)


def select_security_headers(raw: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Filtre les en-têtes de sécurité, dans l'ordre de ``SECURITY_HEADERS``.

    Les en-têtes absents ne sont pas listés (le rapport relève ce qui est
    réellement renvoyé). Les ``Set-Cookie`` suivent, valeur masquée.
    """
    selected: list[tuple[str, str]] = []
    for wanted in SECURITY_HEADERS:
        for name, value in raw:
            if name.lower() == wanted.lower():
                selected.append((wanted, value))
    for name, value in raw:
        if name.lower() == "set-cookie":
            selected.append(("Set-Cookie", redact_cookie(value)))
    return selected


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("`", "'").replace("\n", " ")


def render_section(captures: list[Capture], base_url: str, today: str) -> str:
    """Produit la section Markdown (marqueurs inclus)."""
    lines = [
        BEGIN_MARKER,
        "## Relevé des en-têtes de sécurité",
        "",
        f"Relevé du {today} sur `{base_url}` (derrière nginx), produit par "
        "`tools/security_headers_report.py` (Exigence 15.11). Valeurs exactes, "
        "valeurs des cookies masquées.",
        "",
    ]
    for cap in captures:
        status = "—" if cap.status is None else str(cap.status)
        lines.append(f"### {cap.label} — `{cap.method} {cap.path}` (statut {status})")
        lines.append("")
        if cap.note:
            lines.append(f"> {cap.note}")
            lines.append("")
        if cap.headers:
            lines += ["| En-tête | Valeur |", "|---|---|"]
            lines += [f"| `{n}` | `{_escape_cell(v)}` |" for n, v in cap.headers]
        elif not cap.note:
            lines.append("Aucun en-tête de sécurité renvoyé.")
        lines.append("")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def report_template(today: str) -> str:
    """Gabarit initial du rapport (10 domaines, Constats, Dérogations)."""
    domains = "\n".join(
        f"| {i} | {name} | À renseigner (Constat `SEC-…` ou « aucun constat ») |"
        for i, name in enumerate(DOMAINS, start=1)
    )
    return f"""# Rapport d'audit de sécurité — {today}

## Domaines couverts (Exigence 13.1)

Chaque domaine comporte au moins un Constat ou la mention « aucun constat ».

| # | Domaine | Constats |
|---|---|---|
{domains}

## Constats (Exigences 13.2 à 13.4)

Gravité : `critique`, `élevée`, `moyenne`, `faible`. État : `ouvert`, `corrigé`,
`accepté`.

- Un Constat `critique` ou `élevée` ouvert rend l'audit non terminé ; il passe à
  `corrigé` avec la référence d'un test de non-régression.
- Un Constat `moyenne` ou `faible` non corrigé est `accepté` avec justification,
  nom de la personne qui l'accepte et date.

| Identifiant | Gravité | Périmètre | Preuve reproductible | État | Justification / test de non-régression / accepté par (date) |
|---|---|---|---|---|---|
| SEC-001 | | | | | |

{BEGIN_MARKER}
{END_MARKER}

## Dérogations
"""


def merge_section(document: str, section: str) -> str:
    """Remplace la section délimitée ; l'insère avant « Dérogations » sinon."""
    pattern = re.compile(
        re.escape(BEGIN_MARKER) + r".*?" + re.escape(END_MARKER) + r"\n?", re.DOTALL
    )
    if pattern.search(document):
        return pattern.sub(lambda _m: section, document, count=1)
    marker = "\n## Dérogations"
    idx = document.find(marker)
    if idx == -1:
        return document.rstrip("\n") + "\n\n" + section
    return document[: idx + 1] + section + document[idx:]


def _request(
    base_url: str,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    insecure: bool = False,
    timeout: float = 10.0,
) -> tuple[int, list[tuple[str, str]]]:
    """Requête sans suivre les redirections ; retourne (statut, en-têtes bruts)."""
    parts = urlsplit(base_url)
    host = parts.hostname or "localhost"
    if parts.scheme == "https":
        ctx = ssl.create_default_context()
        if insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(
            host, parts.port or 443, timeout=timeout, context=ctx
        )
    else:
        conn = http.client.HTTPConnection(host, parts.port or 80, timeout=timeout)
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        resp.read()
        return resp.status, resp.getheaders()
    finally:
        conn.close()


def _login(base_url: str, email: str, password: str, insecure: bool) -> str | None:
    """Ouvre une session et retourne l'en-tête ``Cookie`` à rejouer, ou ``None``."""
    payload = json.dumps({"email": email, "password": password}).encode()
    status, raw = _request(
        base_url,
        "POST",
        "/api/v1/auth/login",
        headers={"Content-Type": "application/json"},
        body=payload,
        insecure=insecure,
    )
    if status != 200:
        return None
    pairs = []
    for name, value in raw:
        if name.lower() == "set-cookie":
            m = _COOKIE_VALUE.match(value)
            if m:
                pairs.append(f"{m.group(1)}={m.group(2)}")
    return "; ".join(pairs) or None


def collect(
    base_url: str,
    auth_path: str,
    api_path: str,
    email: str | None,
    password: str | None,
    insecure: bool,
) -> list[Capture]:
    """Relève les trois réponses demandées par l'Exigence 15.11."""
    specs = [
        ("Page d'accueil", "/", False),
        ("Page authentifiée", auth_path, True),
        ("Réponse d'API", api_path, True),
    ]
    cookie: str | None = None
    login_note = ""
    if email and password:
        try:
            cookie = _login(base_url, email, password, insecure)
        except OSError as exc:
            login_note = f"Connexion impossible : {exc}."
        else:
            if cookie is None:
                login_note = "Connexion refusée : réponse authentifiée non relevée."
    else:
        login_note = "Identifiants non fournis : réponse authentifiée non relevée."

    captures: list[Capture] = []
    for label, path, needs_auth in specs:
        cap = Capture(label=label, method="GET", path=path)
        if needs_auth and cookie is None:
            cap.note = login_note
            captures.append(cap)
            continue
        try:
            status, raw = _request(
                base_url,
                "GET",
                path,
                headers={"Cookie": cookie} if needs_auth and cookie else None,
                insecure=insecure,
            )
        except OSError as exc:
            cap.note = f"Requête impossible : {exc}."
        else:
            cap.status = status
            cap.headers = select_security_headers(raw)
            if 300 <= status < 400:
                cap.note = "Redirection non suivie : en-têtes de la réponse de redirection."
        captures.append(cap)
    return captures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="https://localhost", help="URL de nginx")
    parser.add_argument("--auth-path", default="/admin", help="page authentifiée")
    parser.add_argument("--api-path", default="/api/v1/auth/me", help="point d'API")
    parser.add_argument("--email", default=os.environ.get("AUDIT_EMAIL"))
    parser.add_argument("--password", default=os.environ.get("AUDIT_PASSWORD"))
    parser.add_argument("--insecure", action="store_true", help="ignore le certificat TLS")
    parser.add_argument("--date", default=date.today().isoformat(), help="AAAA-MM-JJ")
    parser.add_argument("--output", type=Path, default=None, help="rapport à mettre à jour")
    parser.add_argument("--stdout", action="store_true", help="affiche sans écrire")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    captures = collect(
        args.base_url,
        args.auth_path,
        args.api_path,
        args.email,
        args.password,
        args.insecure,
    )
    section = render_section(captures, args.base_url, args.date)
    if args.stdout:
        sys.stdout.write(section)
        return 0
    output = args.output or Path("docs/security") / f"audit-{args.date}.md"
    if output.exists():
        document = output.read_text(encoding="utf-8")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        document = report_template(args.date)
    output.write_text(merge_section(document, section), encoding="utf-8")
    print(f"Section mise à jour : {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
