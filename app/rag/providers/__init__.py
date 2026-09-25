"""Providers IA (Protocols LLM / Embedding) sélectionnés par configuration.

Ce paquet expose l'abstraction des fournisseurs d'IA (Exigence 16) :

* les contrats :class:`LLMProvider` / :class:`EmbeddingProvider` et le type
  :class:`Message` (dans :mod:`app.rag.providers.base`) ;
* trois familles d'implémentations : ``openai/``, ``anthropic/`` et ``local/`` ;
* une fabrique qui sélectionne l'implémentation d'après la configuration
  (``settings.llm_provider`` / ``settings.embedding_provider``, Exigence 16.3).

Les Providers sont isolés de la couche domaine : aucun import de ``app.models``
ni de ``app.services`` (Exigence 16.4).
"""

from __future__ import annotations

from app.core.config import Settings, settings as default_settings
from app.rag.providers.base import (
    EmbeddingProvider,
    LLMProvider,
    Message,
    Role,
)


def get_llm_provider(config: Settings | None = None) -> LLMProvider:
    """Retourne le :class:`LLMProvider` correspondant à ``settings.llm_provider``.

    Le fournisseur (``openai`` / ``anthropic`` / ``local``) est choisi par
    configuration (Exigence 16.3). L'import de l'implémentation est différé afin
    de ne charger que le provider sélectionné.
    """
    cfg = config or default_settings
    provider = cfg.llm_provider

    if provider == "openai":
        from app.rag.providers.openai import OpenAILLMProvider

        return OpenAILLMProvider(cfg)
    if provider == "anthropic":
        from app.rag.providers.anthropic import AnthropicLLMProvider

        return AnthropicLLMProvider(cfg)
    if provider == "local":
        from app.rag.providers.local import LocalLLMProvider

        return LocalLLMProvider(cfg)

    # Défensif : la config contraint déjà les valeurs via un Literal.
    raise ValueError(f"Fournisseur LLM inconnu : {provider!r}")


def get_embedding_provider(config: Settings | None = None) -> EmbeddingProvider:
    """Retourne l'``EmbeddingProvider`` selon ``settings.embedding_provider``.

    Le fournisseur (``openai`` / ``anthropic`` / ``local``) est choisi par
    configuration (Exigence 16.3). L'import de l'implémentation est différé afin
    de ne charger que le provider sélectionné.
    """
    cfg = config or default_settings
    provider = cfg.embedding_provider

    if provider == "openai":
        from app.rag.providers.openai import OpenAIEmbeddingProvider

        return OpenAIEmbeddingProvider(cfg)
    if provider == "anthropic":
        from app.rag.providers.anthropic import AnthropicEmbeddingProvider

        return AnthropicEmbeddingProvider(cfg)
    if provider == "local":
        from app.rag.providers.local import LocalEmbeddingProvider

        return LocalEmbeddingProvider(cfg)

    # Défensif : la config contraint déjà les valeurs via un Literal.
    raise ValueError(f"Fournisseur d'embeddings inconnu : {provider!r}")


__all__ = [
    "Message",
    "Role",
    "LLMProvider",
    "EmbeddingProvider",
    "get_llm_provider",
    "get_embedding_provider",
]
