"""Utilitaires de texte : normalisation, empreinte et assainissement à l'écriture."""

from __future__ import annotations

import hashlib
import unicodedata


def _normalize_once(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(ch for ch in value if unicodedata.category(ch)[0] not in ("P", "S"))
    value = unicodedata.normalize("NFKC", value)
    return " ".join(value.split())


def normalize_text(value: str) -> str:
    """NFKC → casefold → suppression des catégories Unicode P* / S* → réduction des espaces.

    Itère jusqu'au point fixe afin de garantir l'idempotence.
    """
    current = value
    for _ in range(8):
        nxt = _normalize_once(current)
        if nxt == current:
            break
        current = nxt
    return current


def fingerprint(title: str, body: str) -> str:
    """Empreinte sha256 (hex) de ``normalize_text(title + "\\n" + body)``."""
    return hashlib.sha256(normalize_text(title + "\n" + body).encode("utf-8")).hexdigest()


def sanitize_text(value: str) -> str:
    """Assainit à l'écriture : NFC, sans caractères de contrôle (sauf ``\\n``), strip."""
    value = unicodedata.normalize("NFC", value)
    value = "".join(
        ch for ch in value if ch == "\n" or unicodedata.category(ch) not in ("Cc", "Cs")
    )
    return value.strip()
