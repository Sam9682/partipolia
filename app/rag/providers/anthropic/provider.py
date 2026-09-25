"""Adaptateurs Anthropic des Providers IA (Exigence 16).

Le SDK ``anthropic`` est une dépendance **optionnelle** : son import est effectué
à l'intérieur des méthodes afin que ce module reste importable même en son
absence. Une :class:`ImportError` explicite est levée à l'utilisation si le
paquet n'est pas installé.

Anthropic ne fournit pas d'API d'embeddings ; :class:`AnthropicEmbeddingProvider`
délègue donc à un :class:`~app.rag.providers.base.EmbeddingProvider` injecté
(par défaut le provider local déterministe), afin de satisfaire le contrat sans
coupler le domaine (Exigence 16.4).
"""

from __future__ import annotations

from typing import cast

from app.core.config import Settings, settings as default_settings
from app.rag.providers.base import EmbeddingProvider, Message

_MISSING_SDK_MESSAGE = (
    "Le SDK 'anthropic' est requis pour ce provider. "
    "Installez la dépendance optionnelle : `uv pip install .[anthropic]`."
)


class AnthropicLLMProvider:
    """LLMProvider s'appuyant sur l'API Messages d'Anthropic (Exigence 16.1).

    La consigne système est extraite des messages et transmise via le paramètre
    ``system`` dédié ; les autres messages sont relayés tels quels.
    """

    # Anthropic exige un plafond de tokens ; valeur de repli raisonnable.
    _DEFAULT_MAX_TOKENS = 1024

    def __init__(self, config: Settings | None = None) -> None:
        self._settings = config or default_settings

    def _client(self) -> object:
        try:
            from anthropic import Anthropic  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - dépend de l'environnement
            raise ImportError(_MISSING_SDK_MESSAGE) from exc
        return Anthropic(api_key=self._settings.llm_api_key.get_secret_value())

    def generate(self, messages: list[Message], temperature: float) -> str:
        """Génère une réponse via l'API Anthropic Messages."""
        client = self._client()
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        conversation = [
            {"role": m["role"], "content": m["content"]}
            for m in messages
            if m["role"] != "system"
        ]
        response = client.messages.create(  # type: ignore[attr-defined]
            model=self._settings.llm_model,
            system=system,
            messages=conversation,
            temperature=temperature,
            max_tokens=self._DEFAULT_MAX_TOKENS,
        )
        # Le contenu est une liste de blocs ; on concatène les blocs texte.
        chunks = [
            getattr(block, "text", "")
            for block in response.content
            if getattr(block, "type", None) == "text"
        ]
        return cast(str, "".join(chunks))


class AnthropicEmbeddingProvider:
    """EmbeddingProvider de repli pour Anthropic (Exigence 16.2).

    Anthropic n'offrant pas d'embeddings, on délègue à un provider d'embeddings
    compatible injecté. Par défaut, le provider local déterministe est utilisé.
    """

    def __init__(
        self,
        config: Settings | None = None,
        *,
        delegate: EmbeddingProvider | None = None,
    ) -> None:
        self._settings = config or default_settings
        if delegate is None:
            # Import différé pour éviter tout cycle et rester léger.
            from app.rag.providers.local.provider import LocalEmbeddingProvider

            delegate = LocalEmbeddingProvider(self._settings)
        self._delegate = delegate

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Délègue le calcul des embeddings au provider sous-jacent."""
        return self._delegate.embed(texts)


__all__ = ["AnthropicLLMProvider", "AnthropicEmbeddingProvider"]
