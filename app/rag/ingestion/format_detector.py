"""Détection du format d'un Document brut (Exigences 11.1, 11.2).

Deuxième étape du pipeline : classe un :class:`RawDocument` dans l'un des quatre
formats pris en charge en V1 — ``HTML``, ``PDF``, ``TXT``, ``CSV`` (Exigence
11.2). La détection combine, par ordre de priorité :

1. le ``Content-Type`` renvoyé par le serveur ;
2. l'extension de l'URL ;
3. une inspection de contenu (signature PDF ``%PDF``, balises HTML, virgules
   régulières pour le CSV) en dernier recours.
"""

from __future__ import annotations

from enum import Enum
from urllib.parse import urlparse


class DocFormat(str, Enum):
    """Formats de Document pris en charge en V1 (Exigence 11.2)."""

    HTML = "HTML"
    PDF = "PDF"
    TXT = "TXT"
    CSV = "CSV"


# Correspondances ``Content-Type`` → format (sous-chaînes recherchées).
_CONTENT_TYPE_HINTS: tuple[tuple[str, DocFormat], ...] = (
    ("application/pdf", DocFormat.PDF),
    ("text/html", DocFormat.HTML),
    ("application/xhtml", DocFormat.HTML),
    ("text/csv", DocFormat.CSV),
    ("application/csv", DocFormat.CSV),
    ("text/plain", DocFormat.TXT),
)

# Correspondances extension d'URL → format.
_EXTENSION_HINTS: dict[str, DocFormat] = {
    ".pdf": DocFormat.PDF,
    ".html": DocFormat.HTML,
    ".htm": DocFormat.HTML,
    ".csv": DocFormat.CSV,
    ".txt": DocFormat.TXT,
    ".text": DocFormat.TXT,
}


class FormatDetector:
    """Détecte le :class:`DocFormat` d'un :class:`RawDocument` (Exigence 11.2)."""

    def detect(self, raw: "RawDocument") -> DocFormat:
        """Retourne le format détecté ; ``TXT`` par défaut (format neutre)."""
        # 1) Indice le plus fiable : le Content-Type déclaré par le serveur.
        if raw.content_type:
            ctype = raw.content_type.lower()
            for needle, fmt in _CONTENT_TYPE_HINTS:
                if needle in ctype:
                    return fmt

        # 2) Extension de l'URL.
        path = urlparse(raw.url).path.lower()
        for ext, fmt in _EXTENSION_HINTS.items():
            if path.endswith(ext):
                return fmt

        # 3) Inspection du contenu (heuristique de repli).
        return self._sniff_content(raw.content)

    @staticmethod
    def _sniff_content(content: bytes) -> DocFormat:
        """Devine le format à partir des premiers octets du contenu."""
        if content.startswith(b"%PDF"):
            return DocFormat.PDF

        # Décodage tolérant pour les heuristiques textuelles.
        head = content[:4096].decode("utf-8", errors="ignore").lstrip().lower()
        if head.startswith("<!doctype html") or head.startswith("<html") or "<body" in head:
            return DocFormat.HTML

        # CSV : plusieurs lignes non vides comportant le même nombre (>1) de virgules.
        sample = content[:4096].decode("utf-8", errors="ignore")
        lines = [line for line in sample.splitlines() if line.strip()]
        if len(lines) >= 2:
            comma_counts = {line.count(",") for line in lines[:5]}
            if comma_counts and 0 not in comma_counts and len(comma_counts) == 1:
                return DocFormat.CSV

        # Défaut neutre : texte brut.
        return DocFormat.TXT


from app.rag.ingestion.types import RawDocument  # noqa: E402

__all__ = ["DocFormat", "FormatDetector"]
