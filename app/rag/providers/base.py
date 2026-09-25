"""Contrats des Providers IA — Protocols LLM et Embedding (Exigence 16).

Ce module définit l'abstraction des fournisseurs d'IA sous forme de ``Protocol``
(typage structurel), afin que le module RAG dépende de contrats et non
d'implémentations concrètes. Aucune dépendance vers la couche domaine
(``app.models`` / ``app.services``) n'est autorisée ici — les Providers restent
isolés du domaine (Exigence 16.4).

* :class:`LLMProvider` — génération de texte : ``generate(messages, temperature)``
  (Exigence 16.1).
* :class:`EmbeddingProvider` — vectorisation : ``embed(texts)`` (Exigence 16.2).

Le fournisseur et le modèle sont sélectionnés par configuration
(``LLM_PROVIDER`` / ``EMBEDDING_PROVIDER``, cf. :mod:`app.core.config`), la
fabrique se trouvant dans :mod:`app.rag.providers` (Exigence 16.3).
"""

from __future__ import annotations

from typing import Literal, Protocol, TypedDict, runtime_checkable

# Rôles reconnus d'un message de conversation, alignés sur les conventions LLM usuelles.
Role = Literal["system", "user", "assistant"]


class Message(TypedDict):
    """Message unitaire d'une conversation transmis au :class:`LLMProvider`.

    ``role`` identifie l'émetteur (consigne système, utilisateur, assistant) et
    ``content`` porte le texte associé.
    """

    role: Role
    content: str


@runtime_checkable
class LLMProvider(Protocol):
    """Fournisseur de génération de texte (Exigence 16.1).

    Une implémentation prend une liste de :class:`Message` et une ``temperature``
    et renvoie le texte généré. Le contrat est volontairement minimal pour rester
    indépendant du fournisseur sous-jacent.
    """

    def generate(self, messages: list[Message], temperature: float) -> str:
        """Génère une réponse texte à partir des ``messages`` fournis."""
        ...


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Fournisseur de vecteurs d'embeddings (Exigence 16.2).

    Une implémentation prend une liste de textes et renvoie, pour chacun, un
    vecteur de flottants. Tous les vecteurs d'un même fournisseur partagent la
    même dimension (``settings.embedding_dim``).
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Calcule le vecteur d'embedding de chaque texte de ``texts``."""
        ...


__all__ = ["Message", "Role", "LLMProvider", "EmbeddingProvider"]
