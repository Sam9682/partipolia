"""Garde-fou d'URL cible des tests de charge (Exigence 16.11).

Si l'URL cible n'est pas explicitement autorisée dans ``loadtest/config.toml``,
aucune requête n'est émise, le code de sortie est non nul et un message d'erreur
est affiché. La comparaison porte sur l'origine exacte (schéma, hôte, port).
"""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_CONFIG = Path(__file__).with_name("config.toml")
_DEFAULT_PORTS = {"http": 80, "https": 443}


class TargetNotAllowedError(Exception):
    """L'URL cible n'est pas autorisée par la configuration."""


def _origin(url: str) -> tuple[str, str, int] | None:
    """Retourne (schéma, hôte, port) normalisés, ou None si l'URL est inexploitable."""
    try:
        parts = urlsplit(url.strip())
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host or port is None or parts.username or parts.password:
        return None
    return scheme, host, port


def load_allowed_urls(config_path: Path = DEFAULT_CONFIG) -> list[str]:
    """Lit la liste ``allowed_urls``. Fichier absent ou invalide : liste vide (tout refusé)."""
    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return []
    urls = data.get("allowed_urls", [])
    return [u for u in urls if isinstance(u, str)] if isinstance(urls, list) else []


def is_allowed(target_url: str, allowed_urls: list[str]) -> bool:
    """Vrai si l'origine de ``target_url`` correspond exactement à une origine autorisée."""
    target = _origin(target_url) if isinstance(target_url, str) else None
    if target is None:
        return False
    return any(_origin(a) == target for a in allowed_urls)


def ensure_allowed(target_url: str, config_path: Path = DEFAULT_CONFIG) -> None:
    """Lève ``TargetNotAllowedError`` si l'URL n'est pas autorisée."""
    if not is_allowed(target_url, load_allowed_urls(config_path)):
        raise TargetNotAllowedError(
            f"URL cible non autorisée : {target_url!r}. "
            f"Ajoutez-la explicitement à {config_path.name} (allowed_urls). Aucune requête émise."
        )


def main(argv: list[str] | None = None) -> int:
    """CLI : ``python -m loadtest.guard <url>`` ; 0 si autorisée, 1 sinon."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("Usage : python -m loadtest.guard <url-cible>", file=sys.stderr)
        return 2
    try:
        ensure_allowed(args[0])
    except TargetNotAllowedError as exc:
        print(f"ERREUR : {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
