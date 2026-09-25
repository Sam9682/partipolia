"""Nettoyage/normalisation du texte extrait (Exigence 11.1).

Quatrième étape du pipeline : normalise le texte issu de l'extraction avant la
déduplication et le chunking. Le nettoyage est *déterministe* et *idempotent*
(``clean(clean(t)) == clean(t)``), propriété clé pour l'idempotence de
l'ingestion par checksum (Property 5) : deux téléchargements du même contenu
produisent le même texte nettoyé, donc le même checksum.

Opérations appliquées :
* normalisation Unicode (NFC) ;
* uniformisation des fins de ligne (``\\r\\n`` / ``\\r`` → ``\\n``) ;
* suppression des espaces en fin de ligne ;
* réduction des séquences d'espaces/tabulations internes à un seul espace ;
* réduction de trois sauts de ligne ou plus à deux (séparation de paragraphes) ;
* rognage des espaces en tête et fin de texte.
"""

from __future__ import annotations

import re
import unicodedata

# Espaces/tabulations répétés (hors sauts de ligne) → un seul espace.
_INLINE_WHITESPACE = re.compile(r"[ \t\f\v]+")
# Espaces en fin de ligne.
_TRAILING_WHITESPACE = re.compile(r"[ \t\f\v]+\n")
# Trois sauts de ligne consécutifs ou plus → deux (séparateur de paragraphe).
_EXCESS_NEWLINES = re.compile(r"\n{3,}")


class TextCleaner:
    """Normalise le texte extrait de manière déterministe et idempotente."""

    def clean(self, text: str) -> str:
        """Retourne une version normalisée de ``text``."""
        if not text:
            return ""

        # Normalisation Unicode canonique.
        normalized = unicodedata.normalize("NFC", text)
        # Fins de ligne uniformisées.
        normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
        # Espaces internes réduits.
        normalized = _INLINE_WHITESPACE.sub(" ", normalized)
        # Espaces en fin de ligne supprimés.
        normalized = _TRAILING_WHITESPACE.sub("\n", normalized)
        # Paragraphes : au plus une ligne vide entre blocs.
        normalized = _EXCESS_NEWLINES.sub("\n\n", normalized)
        # Rognage global.
        return normalized.strip()


__all__ = ["TextCleaner"]
