"""Générateur de données de charge déterministe et nettoyage (Exigences 16.8, 16.9, 16.12).

- Même graine et même volume : données strictement identiques (16.8).
- Tous les objets portent le préfixe ``loadtest+<seed>-`` (email des comptes, noms des objets).
- ``run_with_cleanup`` supprime les données dans un ``finally`` quelle que soit l'issue (16.9) ;
  si le nettoyage échoue ou laisse un reliquat, il est listé et le code de sortie est non nul (16.12).
"""

from __future__ import annotations

import hashlib
import random
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

# Domaine réservé RFC 2606 mais accepté par EmailStr (``.invalid`` est rejeté comme nom spécial).
EMAIL_DOMAIN = "loadtest.example.com"
EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_CLEANUP_FAILED = 3


def prefix_for(seed: int) -> str:
    """Préfixe de nettoyage commun à tous les objets d'une graine."""
    return f"loadtest+{int(seed)}-"


@dataclass(frozen=True)
class LoadAccount:
    email: str
    pseudonym: str
    password: str
    region: str


@dataclass(frozen=True)
class LoadObject:
    """Objet de test (revendication, soutien, participation, scénario…)."""

    kind: str
    name: str
    owner_email: str


@dataclass(frozen=True)
class LoadDataset:
    seed: int
    prefix: str
    accounts: tuple[LoadAccount, ...]
    objects: tuple[LoadObject, ...]


_REGIONS = ("11", "24", "27", "28", "32", "44", "52", "53", "75", "76", "84", "93", "94")
_OBJECT_KINDS = ("claim", "support", "participation", "scenario")


def generate_dataset(seed: int, accounts: int = 10, objects_per_account: int = 2) -> LoadDataset:
    """Génère des comptes et objets de test de façon purement déterministe (aucun état global)."""
    if accounts < 0 or objects_per_account < 0:
        raise ValueError("accounts et objects_per_account doivent être >= 0")
    prefix = prefix_for(seed)
    rng = random.Random(f"loadtest:{int(seed)}")
    accs: list[LoadAccount] = []
    objs: list[LoadObject] = []
    for i in range(accounts):
        email = f"{prefix}{i:06d}@{EMAIL_DOMAIN}"
        pwd = hashlib.sha256(f"{prefix}{i}:pwd".encode()).hexdigest()[:20] + "Aa1!"
        accs.append(LoadAccount(email, f"{prefix}user{i:06d}", pwd, rng.choice(_REGIONS)))
        for j in range(objects_per_account):
            kind = _OBJECT_KINDS[rng.randrange(len(_OBJECT_KINDS))]
            objs.append(LoadObject(kind, f"{prefix}{kind}-{i:06d}-{j:03d}", email))
    return LoadDataset(int(seed), prefix, tuple(accs), tuple(objs))


class LoadBackend(Protocol):
    """Stockage des données de test (base réelle ou double en mémoire)."""

    def insert(self, dataset: LoadDataset) -> None: ...

    def list_prefixed(self, prefix: str) -> list[str]:
        """Identifiants lisibles des objets restants dont le nom/email commence par ``prefix``."""
        ...

    def delete_prefixed(self, prefix: str) -> None: ...


class SqlBackend:
    """Nettoyage SQL par préfixe sur une connexion SQLAlchemy synchrone.

    ``tables`` : (table, colonne texte portant le préfixe). Les tables sont supprimées dans
    l'ordre donné (dépendantes d'abord, ``users`` en dernier). L'insertion reste à la charge
    de l'appelant (via l'API de la Plateforme), ce backend ne sert qu'au nettoyage/inventaire.
    """

    def __init__(self, connection: Any, tables: list[tuple[str, str]] | None = None) -> None:
        self._conn = connection
        self._tables = tables or [("users", "email")]

    def insert(self, dataset: LoadDataset) -> None:  # pragma: no cover - créé via l'API
        raise NotImplementedError("Les données sont créées via l'API de la Plateforme.")

    def list_prefixed(self, prefix: str) -> list[str]:
        from sqlalchemy import text

        found: list[str] = []
        for table, col in self._tables:
            rows = self._conn.execute(
                text(f'SELECT "{col}" FROM "{table}" WHERE "{col}" LIKE :p ESCAPE \'\\\''),
                {"p": _like_escape(prefix) + "%"},
            )
            found += [f"{table}:{r[0]}" for r in rows]
        return found

    def delete_prefixed(self, prefix: str) -> None:
        from sqlalchemy import text

        for table, col in self._tables:
            self._conn.execute(
                text(f'DELETE FROM "{table}" WHERE "{col}" LIKE :p ESCAPE \'\\\''),
                {"p": _like_escape(prefix) + "%"},
            )
        commit = getattr(self._conn, "commit", None)
        if callable(commit):
            commit()


def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@dataclass
class CleanupResult:
    remaining: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return not self.remaining and self.error is None


def cleanup(backend: LoadBackend, seed: int) -> CleanupResult:
    """Supprime exactement les objets préfixés puis vérifie qu'il ne reste rien."""
    prefix = prefix_for(seed)
    result = CleanupResult()
    try:
        backend.delete_prefixed(prefix)
    except Exception as exc:  # noqa: BLE001 - on doit toujours inventorier le reliquat
        result.error = f"{type(exc).__name__}: {exc}"
    try:
        result.remaining = list(backend.list_prefixed(prefix))
    except Exception as exc:  # noqa: BLE001
        result.error = (result.error + " ; " if result.error else "") + f"inventaire impossible : {exc}"
    return result


def run_with_cleanup(
    backend: LoadBackend,
    seed: int,
    run: Callable[[LoadDataset], Any],
    *,
    accounts: int = 10,
    objects_per_account: int = 2,
    out=sys.stderr,
) -> int:
    """Génère, insère, exécute ``run`` puis nettoie dans un ``finally``.

    Retourne 0 si tout va bien, 1 si l'exécution a échoué (nettoyage OK),
    3 si le nettoyage a échoué ou laissé un reliquat (liste affichée).
    """
    dataset = generate_dataset(seed, accounts, objects_per_account)
    code = EXIT_OK
    try:
        backend.insert(dataset)
        run(dataset)
    except Exception as exc:  # noqa: BLE001
        print(f"ERREUR : exécution échouée : {exc}", file=out)
        code = EXIT_RUN_FAILED
    finally:
        result = cleanup(backend, seed)
        if not result.ok:
            print("ERREUR : nettoyage incomplet des données de test.", file=out)
            if result.error:
                print(f"  cause : {result.error}", file=out)
            for item in result.remaining:
                print(f"  reliquat : {item}", file=out)
            code = EXIT_CLEANUP_FAILED
    return code
