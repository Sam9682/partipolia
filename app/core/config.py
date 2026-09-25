"""Configuration applicative par variables d'environnement (Exigences 16.3, 28.6, 33.1).

Toute la configuration de PARTIPOLAI est centralisée ici via un unique objet
:class:`Settings` (Pydantic v2 / ``pydantic-settings``). Les valeurs sont lues depuis
l'environnement puis, en repli, depuis un fichier ``.env`` (voir ``.env.example``).

Principes directeurs :

* **Aucun secret en dur** — les champs sensibles (``JWT_SECRET_KEY``, clés d'API, mots de
  passe SMTP) n'ont pas de valeur par défaut exploitable en production ; ils doivent être
  fournis par l'environnement (Exigences 28.6, 32.6).
* **Providers IA configurables** — fournisseur et modèle de génération et d'embeddings,
  ainsi que la dimension du vecteur, sont pilotés par variables d'environnement
  (Exigence 16.3).
* **Poids de fusion RAG configurables** — les quatre poids de la recherche hybride sont
  exposés et validés (leur somme doit valoir 1.0 — cf. Property 8 / Exigence 12.2).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Fournisseurs IA reconnus (sélection par configuration — Exigence 16).
ProviderName = Literal["openai", "anthropic", "local"]


class Settings(BaseSettings):
    """Paramètres applicatifs chargés depuis l'environnement / le fichier ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Application -------------------------------------------------------
    app_env: Literal["development", "test", "staging", "production"] = Field(
        default="development", alias="APP_ENV"
    )
    app_debug: bool = Field(default=False, alias="APP_DEBUG")
    app_base_url: str = Field(default="http://localhost:8000", alias="APP_BASE_URL")

    # --- Base de données (SQLAlchemy 2.x async + psycopg 3 + pgvector) -----
    database_url: str = Field(
        default="postgresql+psycopg://partipolia:partipolia@localhost:5432/partipolia",
        alias="DATABASE_URL",
    )

    # --- Redis (cache, broker Celery, rate limit) --------------------------
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    # --- Limitation de débit (RateLimiter Redis) — Exigences 26.4, 26.5 ----
    # Activation globale : permet de désactiver la limitation (tests, dev) sans
    # retirer les dépendances des routeurs.
    rate_limit_enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    # Fenêtre de comptage commune (en secondes) — modèle « fixed window ».
    rate_limit_window_seconds: int = Field(
        default=60, ge=1, alias="RATE_LIMIT_WINDOW_SECONDS"
    )
    # Seuil par défaut appliqué aux points d'accès sensibles (login, register,
    # proposals, comments, votes) — nombre de requêtes autorisées par fenêtre.
    rate_limit_default_max: int = Field(
        default=30, ge=1, alias="RATE_LIMIT_DEFAULT_MAX"
    )
    # Seuil renforcé du point d'accès ``POST /api/v1/chat`` (protection accrue
    # contre les abus — Exigence 26.5) : volontairement plus bas que le défaut.
    rate_limit_chat_max: int = Field(default=10, ge=1, alias="RATE_LIMIT_CHAT_MAX")

    # --- JWT (authentification) --------------------------------------------
    # Aucun secret par défaut : doit être fourni par l'environnement (Exigence 28.6).
    jwt_secret_key: SecretStr = Field(default=SecretStr(""), alias="JWT_SECRET_KEY")
    jwt_algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    jwt_access_token_expire_minutes: int = Field(
        default=15, ge=1, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES"
    )
    jwt_refresh_token_expire_days: int = Field(
        default=14, ge=1, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS"
    )

    # --- Fournisseur LLM (génération) — configurable (Exigence 16.3) -------
    llm_provider: ProviderName = Field(default="openai", alias="LLM_PROVIDER")
    llm_model: str = Field(default="gpt-4o-mini", alias="LLM_MODEL")
    llm_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_API_KEY")
    llm_temperature: float = Field(default=0.1, ge=0.0, le=2.0, alias="LLM_TEMPERATURE")

    # --- Fournisseur d'embeddings — configurable (Exigence 16.3) -----------
    embedding_provider: ProviderName = Field(default="openai", alias="EMBEDDING_PROVIDER")
    embedding_model: str = Field(default="text-embedding-3-small", alias="EMBEDDING_MODEL")
    embedding_api_key: SecretStr = Field(default=SecretStr(""), alias="EMBEDDING_API_KEY")
    # Dimension du vecteur pgvector — configurable, défaut 1536 (Exigence 11.6).
    embedding_dim: int = Field(default=1536, gt=0, alias="EMBEDDING_DIM")

    # --- RAG (recherche hybride, fusion, reranking, chunking) --------------
    rag_top_k: int = Field(default=20, gt=0, alias="RAG_TOP_K")
    rag_rerank_keep: int = Field(default=6, gt=0, alias="RAG_RERANK_KEEP")
    rag_chunk_size_tokens: int = Field(default=800, gt=0, alias="RAG_CHUNK_SIZE_TOKENS")
    rag_chunk_overlap_tokens: int = Field(default=120, ge=0, alias="RAG_CHUNK_OVERLAP_TOKENS")
    # Poids de fusion des scores — configurables ; leur somme doit valoir 1.0 (Property 8).
    rag_weight_semantic: float = Field(default=0.40, ge=0.0, le=1.0, alias="RAG_WEIGHT_SEMANTIC")
    rag_weight_lexical: float = Field(default=0.30, ge=0.0, le=1.0, alias="RAG_WEIGHT_LEXICAL")
    rag_weight_source_quality: float = Field(
        default=0.20, ge=0.0, le=1.0, alias="RAG_WEIGHT_SOURCE_QUALITY"
    )
    rag_weight_recency: float = Field(default=0.10, ge=0.0, le=1.0, alias="RAG_WEIGHT_RECENCY")

    # --- SMTP (vérification, notifications) --------------------------------
    smtp_host: str = Field(default="localhost", alias="SMTP_HOST")
    smtp_port: int = Field(default=1025, gt=0, le=65535, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: SecretStr = Field(default=SecretStr(""), alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="no-reply@partipolia.local", alias="SMTP_FROM")
    smtp_tls: bool = Field(default=False, alias="SMTP_TLS")

    # --- CORS --------------------------------------------------------------
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:8000", "http://localhost:3000"],
        alias="CORS_ALLOW_ORIGINS",
    )
    cors_allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    cors_allow_methods: list[str] = Field(
        default_factory=lambda: ["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        alias="CORS_ALLOW_METHODS",
    )
    cors_allow_headers: list[str] = Field(
        default_factory=lambda: ["*"], alias="CORS_ALLOW_HEADERS"
    )

    # ------------------------------------------------------------------ #
    # Validateurs                                                        #
    # ------------------------------------------------------------------ #
    @field_validator(
        "cors_allow_origins",
        "cors_allow_methods",
        "cors_allow_headers",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Autorise une liste séparée par des virgules dans une variable d'environnement.

        ``CORS_ALLOW_ORIGINS=http://a,http://b`` devient ``["http://a", "http://b"]``.
        Une valeur déjà de type liste est renvoyée telle quelle.
        """
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _check_fusion_weights_sum_to_one(self) -> "Settings":
        """Vérifie que la somme des poids de fusion vaut 1.0 (Property 8 / Exigence 12.2)."""
        total = (
            self.rag_weight_semantic
            + self.rag_weight_lexical
            + self.rag_weight_source_quality
            + self.rag_weight_recency
        )
        # Tolérance flottante minime pour éviter les faux négatifs (ex. 0.1 + 0.2).
        if abs(total - 1.0) > 1e-9:
            raise ValueError(
                "La somme des poids de fusion RAG doit valoir 1.0 "
                f"(actuellement {total!r}). Ajustez RAG_WEIGHT_SEMANTIC, RAG_WEIGHT_LEXICAL, "
                "RAG_WEIGHT_SOURCE_QUALITY et RAG_WEIGHT_RECENCY."
            )
        return self

    @model_validator(mode="after")
    def _check_secrets_in_production(self) -> "Settings":
        """Interdit les secrets vides ou factices en production (Exigence 28.6)."""
        if self.app_env == "production" and not self.jwt_secret_key.get_secret_value():
            raise ValueError(
                "JWT_SECRET_KEY doit être défini via l'environnement en production."
            )
        return self

    # ------------------------------------------------------------------ #
    # Propriétés dérivées                                                #
    # ------------------------------------------------------------------ #
    @property
    def fusion_weights(self) -> dict[str, float]:
        """Poids de fusion sous forme de dictionnaire, prêts pour ``ScoreFusion``."""
        return {
            "semantic": self.rag_weight_semantic,
            "lexical": self.rag_weight_lexical,
            "source_quality": self.rag_weight_source_quality,
            "recency": self.rag_weight_recency,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Retourne l'instance unique de :class:`Settings` (mémoïsée)."""
    return Settings()


# Instance partagée pour un import direct pratique (``from app.core.config import settings``).
settings: Settings = get_settings()
