"""Extraction du texte d'un Document brut selon son format (Exigence 11.1, 11.2).

Troisième étape du pipeline : transforme un :class:`RawDocument` en texte brut
en fonction du :class:`DocFormat` détecté.

* ``HTML`` → texte visible via BeautifulSoup (balises ``script``/``style``
  retirées).
* ``PDF``  → concaténation du texte de chaque page via ``pypdf``.
* ``CSV``  → contenu textuel tel quel (le nettoyage/normalisation est délégué à
  :class:`TextCleaner`).
* ``TXT``  → décodage du contenu en UTF-8 (tolérant).

L'import des bibliothèques d'extraction (``bs4``, ``pypdf``) est différé afin que
le module reste importable même si une dépendance optionnelle n'est pas encore
installée (utile en environnement de test/CI restreint).
"""

from __future__ import annotations

import io

from app.rag.ingestion.format_detector import DocFormat


class TextExtractor:
    """Extrait le texte d'un :class:`RawDocument` selon le format fourni."""

    def extract(self, raw: "RawDocument", fmt: DocFormat) -> str:
        """Retourne le texte extrait pour le format ``fmt``."""
        if fmt is DocFormat.PDF:
            return self._extract_pdf(raw.content)
        if fmt is DocFormat.HTML:
            return self._extract_html(raw.content)
        # TXT et CSV : décodage direct ; la structure CSV est préservée telle
        # quelle et nettoyée en aval par TextCleaner.
        return self._decode(raw.content)

    @staticmethod
    def _decode(content: bytes) -> str:
        """Décode des octets en UTF-8 de façon tolérante."""
        return content.decode("utf-8", errors="replace")

    @classmethod
    def _extract_html(cls, content: bytes) -> str:
        """Extrait le texte visible d'un document HTML."""
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(cls._decode(content), "html.parser")
        # Retire le contenu non textuel (scripts, styles).
        for element in soup(["script", "style", "noscript"]):
            element.decompose()
        return soup.get_text(separator="\n")

    @classmethod
    def _extract_pdf(cls, content: bytes) -> str:
        """Extrait le texte page par page d'un PDF."""
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(content))
        pages: list[str] = []
        for page in reader.pages:
            pages.append(page.extract_text() or "")
        return "\n".join(pages)


from app.rag.ingestion.types import RawDocument  # noqa: E402

__all__ = ["TextExtractor"]
