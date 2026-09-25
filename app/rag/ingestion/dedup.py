"""Déduplication des Documents par checksum (Exigence 11.3, Property 5).

Cinquième étape du pipeline : calcule un checksum *déterministe* du contenu
nettoyé et indique si ce checksum est déjà connu, afin d'éviter la réindexation
d'un Document identique (``documents.checksum`` UNIQUE).

Le calcul du checksum est une logique pure (SHA-256, hexadécimal), donc
reproductible d'une exécution à l'autre : deux ingestions du même contenu
produisent le même checksum (base de l'idempotence, Property 5).

Le test « déjà connu » (:meth:`Deduplicator.is_known`) délègue la vérification
d'existence à un *callback* injecté (par ex. une requête
``SELECT 1 FROM documents WHERE checksum = :c``). Ce module reste ainsi isolé de
la couche domaine et testable sans base : l'orchestrateur Celery fournit le
callback réel (tâche 6.6).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable

# Type du callback vérifiant l'existence d'un checksum en base.
ChecksumLookup = Callable[[str], bool]


class Deduplicator:
    """Checksum déterministe + test d'existence (Exigence 11.3)."""

    def __init__(self, lookup: ChecksumLookup | None = None) -> None:
        """``lookup`` : fonction ``checksum -> bool`` (True si déjà enregistré).

        Si aucun ``lookup`` n'est fourni, :meth:`is_known` renvoie toujours
        ``False`` (aucun checksum connu), ce qui convient pour un calcul de
        checksum isolé ou en test.
        """
        self._lookup = lookup

    def checksum(self, content: str) -> str:
        """Calcule le SHA-256 hexadécimal de ``content`` (déterministe).

        L'encodage UTF-8 garantit un résultat stable indépendant de la
        plateforme. Le résultat fait 64 caractères hexadécimaux, compatible avec
        la colonne ``documents.checksum`` (``String(128)``).
        """
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def is_known(self, checksum: str) -> bool:
        """Indique si ``checksum`` est déjà enregistré (via le callback injecté)."""
        if self._lookup is None:
            return False
        return self._lookup(checksum)


__all__ = ["Deduplicator", "ChecksumLookup"]
