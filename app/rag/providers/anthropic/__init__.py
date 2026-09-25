"""Provider IA « anthropic » — adaptateur du SDK Anthropic (Exigence 16).

L'import du SDK ``anthropic`` (dépendance optionnelle du projet) est différé à
l'exécution des méthodes, de sorte que ce module s'importe même lorsque la
dépendance n'est pas installée.

Anthropic n'exposant pas d'API d'embeddings, :class:`AnthropicEmbeddingProvider`
délègue à une implémentation d'embeddings compatible (par défaut, le provider
local déterministe) tout en respectant le contrat
:class:`~app.rag.providers.base.EmbeddingProvider`. Les implémentations restent
isolées du domaine (Exigence 16.4).
"""

from __future__ import annotations

from app.rag.providers.anthropic.provider import (
    AnthropicEmbeddingProvider,
    AnthropicLLMProvider,
)

__all__ = ["AnthropicLLMProvider", "AnthropicEmbeddingProvider"]
