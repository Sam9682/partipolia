"""Provider IA « openai » — adaptateur du SDK OpenAI (Exigence 16).

L'import du SDK ``openai`` (dépendance optionnelle du projet) est différé à
l'exécution des méthodes, de sorte que ce module s'importe même lorsque la
dépendance n'est pas installée. Les implémentations restent isolées du domaine
(Exigence 16.4).
"""

from __future__ import annotations

from app.rag.providers.openai.provider import (
    OpenAIEmbeddingProvider,
    OpenAILLMProvider,
)

__all__ = ["OpenAILLMProvider", "OpenAIEmbeddingProvider"]
