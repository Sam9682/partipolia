"""Adaptateurs OpenAI des Providers IA (Exigence 16).

Le SDK ``openai`` est une dépendance **optionnelle** : son import est effectué à
l'intérieur des méthodes afin que ce module reste importable même lorsque la
dépendance est absente. Une :class:`ImportError` explicite est levée à
l'utilisation si le paquet n'est pas installé.

Ces adaptateurs ne dépendent que de la configuration et des contrats
(:mod:`app.rag.providers.base`) — aucune importation de la couche domaine
(Exigence 16.4).
"""

from __future__ import annotations

from typing import cast

from app.core.config import Settings, settings as default_settings
from app.rag.providers.base import Message

_MISSING_SDK_MESSAGE = (
    "Le SDK 'openai' est requis pour ce provider. "
    "Installez la dépendance optionnelle : `uv pip install .[openai]`."
)


class OpenAILLMProvider:
    """LLMProvider s'appuyant sur l'API de complétion de chat OpenAI (Exigence 16.1)."""

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings

    def _client(self) -> object:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise ImportError(_MISSING_SDK_MESSAGE) from exc
        return OpenAI(api_key=self._settings.llm_api_key.get_secret_value())

    def generate(self, messages: list[Message], temperature: float) -> str:
        """Génère une réponse via l'API OpenAI Chat Completions."""
        client = self._client()
        response = client.chat.completions.create(  # type: ignore[attr-defined]
            model=self._settings.llm_model,
            messages=[{"role": m["role"], "content": m["content"]} for m in messages],
            temperature=temperature,
        )
        content = response.choices[0].message.content
        return cast(str, content or "")


class OpenAIEmbeddingProvider:
    """EmbeddingProvider s'appuyant sur l'API d'embeddings OpenAI (Exigence 16.2)."""

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings

    def _client(self) -> object:
        try:
            from openai import OpenAI  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise ImportError(_MISSING_SDK_MESSAGE) from exc
        return OpenAI(api_key=self._settings.embedding_api_key.get_secret_value())

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Calcule les embeddings via l'API OpenAI Embeddings."""
        client = self._client()
        response = client.embeddings.create(  # type: ignore[attr-defined]
            model=self._settings.embedding_model,
            input=texts,
        )
        return [list(item.embedding) for item in response.data]


__all__ = ["OpenAILLMProvider", "OpenAIEmbeddingProvider"]
