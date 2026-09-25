"""Implémentations déterministes locales des Providers IA (Exigence 16).

Ces classes n'effectuent aucun appel réseau et ne dépendent d'aucune bibliothèque
optionnelle : elles conviennent comme défaut hors-ligne et comme substituts
déterministes en test. Elles restent isolées du domaine (Exigence 16.4).

* :class:`LocalEmbeddingProvider` — embeddings pseudo-aléatoires mais
  **déterministes** : un même texte produit toujours le même vecteur, de
  dimension ``settings.embedding_dim``. Les vecteurs sont normalisés (norme L2
  unitaire), ce qui rend la similarité cosinus bien définie.
* :class:`LocalLLMProvider` — génération « écho/gabarit » : renvoie un texte
  construit à partir des messages fournis, sans modèle sous-jacent.
"""

from __future__ import annotations

import hashlib
import math
import struct

from app.core.config import Settings, settings as default_settings
from app.rag.providers.base import Message


class LocalEmbeddingProvider:
    """EmbeddingProvider déterministe basé sur un hachage (Exigence 16.2).

    Le vecteur d'un texte est dérivé d'un flux d'octets pseudo-aléatoire ensemencé
    par le SHA-256 du texte : il est donc reproductible d'une exécution à l'autre.
    La dimension provient de ``settings.embedding_dim``.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings
        self._dim = self._settings.embedding_dim

    @property
    def dim(self) -> int:
        """Dimension des vecteurs produits (``settings.embedding_dim``)."""
        return self._dim

    def _vector_for(self, text: str) -> list[float]:
        """Construit un vecteur normalisé et déterministe pour ``text``."""
        values: list[float] = []
        counter = 0
        # On étire le hachage en un flux d'octets suffisant pour ``dim`` flottants.
        while len(values) < self._dim:
            digest = hashlib.sha256(f"{text}\x00{counter}".encode("utf-8")).digest()
            # 32 octets -> 8 flottants 32 bits.
            for offset in range(0, len(digest), 4):
                if len(values) >= self._dim:
                    break
                (raw,) = struct.unpack(">I", digest[offset : offset + 4])
                # Ramène dans [-1, 1].
                values.append((raw / 0xFFFFFFFF) * 2.0 - 1.0)
            counter += 1

        norm = math.sqrt(sum(component * component for component in values))
        if norm == 0.0:
            return values
        return [component / norm for component in values]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Retourne un vecteur normalisé par texte (dimension ``embedding_dim``)."""
        return [self._vector_for(text) for text in texts]


class LocalLLMProvider:
    """LLMProvider déterministe « écho/gabarit » (Exigence 16.1).

    Ne fait appel à aucun modèle : compose une réponse à partir de la dernière
    consigne système et du dernier message utilisateur. Utile comme défaut
    hors-ligne et pour rendre les tests reproductibles.
    """

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings

    def generate(self, messages: list[Message], temperature: float) -> str:
        """Compose une réponse déterministe à partir des ``messages``."""
        if not messages:
            return ""

        system = next(
            (m["content"] for m in reversed(messages) if m["role"] == "system"),
            "",
        )
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "",
        )

        parts: list[str] = []
        if system:
            parts.append(f"[system] {system}")
        parts.append(f"[echo] {last_user}" if last_user else "[echo]")
        return "\n".join(parts)


__all__ = ["LocalEmbeddingProvider", "LocalLLMProvider"]
