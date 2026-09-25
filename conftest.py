"""Configuration racine de la suite de tests.

Ce module s'exécute avant toute collecte pytest (pytest importe le ``conftest.py``
racine en premier). Son unique rôle est de **rendre la suite exécutable dans un
environnement dépourvu des pilotes natifs optionnels** (``psycopg`` 3 et
``pgvector``) : ces paquets ne sont nécessaires qu'à l'exécution réelle contre
PostgreSQL, jamais pour la logique métier pure testée par les tests unitaires et
de propriété.

Le mécanisme est **non intrusif** :

* les stubs ne sont enregistrés **que si le vrai paquet est absent**
  (``importlib.util.find_spec`` renvoie ``None``) ; dans un environnement complet
  (``uv run`` avec toutes les dépendances), aucun stub n'est installé et le vrai
  pilote est utilisé ;
* les stubs ne fournissent que le strict minimum permettant la **construction**
  du moteur SQLAlchemy et la déclaration des modèles ORM ; ils ne simulent aucune
  connexion et lèveraient une erreur si un test tentait un accès réel à la base.

Aucune logique testée n'est remplacée : le vrai code des Services et des schémas
est importé et exercé tel quel.
"""

from __future__ import annotations

import importlib.util
import sys
import types


def _has(module_name: str) -> bool:
    """Indique si ``module_name`` est réellement installé et importable."""
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _install_psycopg_stub() -> None:
    """Enregistre un pilote ``psycopg`` factice suffisant pour créer le moteur.

    SQLAlchemy résout le dialecte ``postgresql+psycopg`` à la **création** du
    moteur (import paresseux du DBAPI). Le stub expose uniquement les attributs
    que le dialecte lit pendant cette phase (``__version__``, ``paramstyle``,
    ``adapters``, ``AsyncConnection``, ``pq.ExecStatus``…). Toute tentative de
    connexion réelle échouerait volontairement.
    """
    psycopg = types.ModuleType("psycopg")

    class _AsyncConnection:
        @staticmethod
        async def connect(*_args: object, **_kwargs: object) -> object:
            raise RuntimeError(
                "psycopg est simulé pour les tests hors-ligne : aucune connexion réelle."
            )

    class _Error(Exception):
        pass

    psycopg.AsyncConnection = _AsyncConnection  # type: ignore[attr-defined]
    psycopg.Connection = object  # type: ignore[attr-defined]
    psycopg.Error = _Error  # type: ignore[attr-defined]
    psycopg.__version__ = "3.2.0"  # type: ignore[attr-defined]
    psycopg.apilevel = "2.0"  # type: ignore[attr-defined]
    psycopg.threadsafety = 2  # type: ignore[attr-defined]
    psycopg.paramstyle = "pyformat"  # type: ignore[attr-defined]
    psycopg.adapters = object()  # type: ignore[attr-defined]

    pq = types.ModuleType("psycopg.pq")

    class ExecStatus:  # noqa: D401 - marqueur factice
        pass

    pq.ExecStatus = ExecStatus  # type: ignore[attr-defined]
    psycopg.pq = pq  # type: ignore[attr-defined]

    adapt_mod = types.ModuleType("psycopg.adapt")

    class AdaptersMap:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

    adapt_mod.AdaptersMap = AdaptersMap  # type: ignore[attr-defined]
    psycopg.adapt = adapt_mod  # type: ignore[attr-defined]

    types_mod = types.ModuleType("psycopg.types")

    class TypeInfo:  # noqa: D401 - marqueur factice
        pass

    types_mod.TypeInfo = TypeInfo  # type: ignore[attr-defined]
    psycopg.types = types_mod  # type: ignore[attr-defined]

    sys.modules["psycopg"] = psycopg
    sys.modules["psycopg.pq"] = pq
    sys.modules["psycopg.adapt"] = adapt_mod
    sys.modules["psycopg.types"] = types_mod


def _install_pgvector_stub() -> None:
    """Enregistre un type de colonne ``pgvector.sqlalchemy.Vector`` factice.

    Seule la **déclaration** de la colonne ``document_chunks.embedding`` a besoin
    de ce type au moment de l'import des modèles ORM ; aucune opération vectorielle
    n'est exécutée par les tests unitaires/propriété.
    """
    from sqlalchemy.types import UserDefinedType

    pgvector = types.ModuleType("pgvector")
    pgv_sa = types.ModuleType("pgvector.sqlalchemy")

    class Vector(UserDefinedType):  # type: ignore[type-arg]
        cache_ok = True

        def __init__(self, dim: int | None = None) -> None:
            self.dim = dim

        def get_col_spec(self, **_kw: object) -> str:
            return "VECTOR" if self.dim is None else f"VECTOR({self.dim})"

    pgv_sa.Vector = Vector  # type: ignore[attr-defined]
    pgvector.sqlalchemy = pgv_sa  # type: ignore[attr-defined]

    sys.modules["pgvector"] = pgvector
    sys.modules["pgvector.sqlalchemy"] = pgv_sa


def _install_argon2_stub() -> None:
    """Enregistre un ``argon2`` factice suffisant pour importer l'``AuthService``.

    ``AuthService`` importe ``PasswordHasher`` et trois exceptions au chargement
    du module. Ces symboles sont nécessaires à l'**import** de la couche API (via
    ``app.api.deps``) que les tests d'API exercent ; le hachage réel n'est jamais
    invoqué par les tests d'API (le Service est surchargé). Le stub fournit un
    hacheur trivial déterministe et des exceptions vides, uniquement pour rendre
    l'import possible hors-ligne.
    """
    argon2 = types.ModuleType("argon2")

    class PasswordHasher:  # noqa: D401 - hacheur factice
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def hash(self, plain: str) -> str:
            return f"argon2-stub${plain}"

        def verify(self, hashed: str, plain: str) -> bool:
            return hashed == f"argon2-stub${plain}"

    exceptions = types.ModuleType("argon2.exceptions")

    class _Argon2Error(Exception):
        pass

    class InvalidHashError(_Argon2Error):
        pass

    class VerificationError(_Argon2Error):
        pass

    class VerifyMismatchError(VerificationError):
        pass

    exceptions.InvalidHashError = InvalidHashError  # type: ignore[attr-defined]
    exceptions.VerificationError = VerificationError  # type: ignore[attr-defined]
    exceptions.VerifyMismatchError = VerifyMismatchError  # type: ignore[attr-defined]

    argon2.PasswordHasher = PasswordHasher  # type: ignore[attr-defined]
    argon2.exceptions = exceptions  # type: ignore[attr-defined]

    sys.modules["argon2"] = argon2
    sys.modules["argon2.exceptions"] = exceptions


def _install_structlog_stub() -> None:
    """Enregistre un ``structlog`` factice suffisant pour importer ``app.core.logging``.

    ``app.core.logging`` importe ``structlog`` (et ``structlog.types``) au
    chargement et l'utilise via ``get_logger(...).bind(...).info(...)``. Ce paquet
    est une dépendance réelle du projet (cf. ``pyproject.toml``) ; le stub n'est
    installé **que s'il est absent** de l'environnement, afin de rendre la logique
    métier (dont l'endpoint chat qui journalise) testable hors-ligne sans simuler
    le rendu structuré. Le stub fournit un logger silencieux et les symboles lus à
    l'import ; il ne produit aucune sortie et ne remplace aucune logique testée.
    """
    structlog = types.ModuleType("structlog")

    class _BoundLogger:
        """Logger factice : ``bind`` renvoie lui-même, les méthodes de log sont muettes."""

        def bind(self, **_kwargs: object) -> "_BoundLogger":
            return self

        def _noop(self, *_args: object, **_kwargs: object) -> None:
            return None

        # Niveaux usuels de journalisation.
        debug = info = warning = error = critical = exception = _noop

    def get_logger(*_args: object, **_kwargs: object) -> _BoundLogger:
        return _BoundLogger()

    def make_filtering_bound_logger(*_args: object, **_kwargs: object) -> type:
        return _BoundLogger

    def configure(*_args: object, **_kwargs: object) -> None:
        return None

    structlog.get_logger = get_logger  # type: ignore[attr-defined]
    structlog.make_filtering_bound_logger = make_filtering_bound_logger  # type: ignore[attr-defined]
    structlog.configure = configure  # type: ignore[attr-defined]

    # Sous-modules et symboles lus à l'import de ``app.core.logging``.
    def _passthrough_processor(*_args: object, **_kwargs: object) -> object:
        return lambda *a, **k: (a[-1] if a else {})

    contextvars = types.ModuleType("structlog.contextvars")
    contextvars.merge_contextvars = _passthrough_processor()  # type: ignore[attr-defined]
    contextvars.bind_contextvars = lambda **kw: dict(kw)  # type: ignore[attr-defined]
    contextvars.clear_contextvars = lambda *a, **k: None  # type: ignore[attr-defined]
    structlog.contextvars = contextvars  # type: ignore[attr-defined]

    processors = types.ModuleType("structlog.processors")
    processors.add_log_level = _passthrough_processor()  # type: ignore[attr-defined]
    processors.StackInfoRenderer = lambda *a, **k: _passthrough_processor()  # type: ignore[attr-defined]
    processors.format_exc_info = _passthrough_processor()  # type: ignore[attr-defined]
    processors.JSONRenderer = lambda *a, **k: _passthrough_processor()  # type: ignore[attr-defined]

    class _TimeStamper:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        def __call__(self, *_a: object, **_k: object) -> object:
            return _a[-1] if _a else {}

    processors.TimeStamper = _TimeStamper  # type: ignore[attr-defined]
    structlog.processors = processors  # type: ignore[attr-defined]

    dev = types.ModuleType("structlog.dev")
    dev.ConsoleRenderer = lambda *a, **k: _passthrough_processor()  # type: ignore[attr-defined]
    structlog.dev = dev  # type: ignore[attr-defined]

    stdlib = types.ModuleType("structlog.stdlib")

    class _ProcessorFormatter:
        def __init__(self, *_a: object, **_k: object) -> None:
            pass

        @staticmethod
        def wrap_for_formatter(*_a: object, **_k: object) -> object:
            return None

        @staticmethod
        def remove_processors_meta(*_a: object, **_k: object) -> object:
            return None

    stdlib.ProcessorFormatter = _ProcessorFormatter  # type: ignore[attr-defined]
    stdlib.LoggerFactory = lambda *a, **k: (lambda *aa, **kk: _BoundLogger())  # type: ignore[attr-defined]
    structlog.stdlib = stdlib  # type: ignore[attr-defined]

    types_mod = types.ModuleType("structlog.types")
    types_mod.EventDict = dict  # type: ignore[attr-defined]
    types_mod.Processor = object  # type: ignore[attr-defined]
    structlog.types = types_mod  # type: ignore[attr-defined]

    sys.modules["structlog"] = structlog
    sys.modules["structlog.contextvars"] = contextvars
    sys.modules["structlog.processors"] = processors
    sys.modules["structlog.dev"] = dev
    sys.modules["structlog.stdlib"] = stdlib
    sys.modules["structlog.types"] = types_mod


# Enregistrement conditionnel : uniquement si le vrai paquet est absent.
if not _has("psycopg"):
    _install_psycopg_stub()

if not _has("pgvector"):
    _install_pgvector_stub()

if not _has("argon2"):
    _install_argon2_stub()

if not _has("structlog"):
    _install_structlog_stub()
