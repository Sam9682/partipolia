"""Provider IA « local » — implémentation déterministe sans dépendance externe.

Ce sous-paquet fournit des implémentations légères et déterministes de
:class:`~app.rag.providers.base.LLMProvider` et
:class:`~app.rag.providers.base.EmbeddingProvider`. Elles ne réalisent aucun
appel réseau : elles servent de défaut hors-ligne et de support de tests
(les propriétés RAG sont testées avec des Providers simulés).

Voir :mod:`app.rag.providers.local.provider` pour le détail.
"""

from __future__ import annotations

from app.rag.providers.local.provider import (
    LocalEmbeddingProvider,
    LocalLLMProvider,
)

__all__ = ["LocalLLMProvider", "LocalEmbeddingProvider"]
