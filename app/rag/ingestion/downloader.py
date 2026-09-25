"""Téléchargement d'une URL de Document (Exigence 11.1).

Première étape du pipeline : récupère le contenu brut d'une URL sous forme de
:class:`RawDocument`. Le téléchargement HTTP est synchrone (le pipeline tourne
dans un worker Celery, hors du chemin de requête FastAPI, Exigence 23.3) et
conserve le ``Content-Type`` renvoyé, qui sert d'indice principal à la détection
de format en aval.
"""

from __future__ import annotations

import httpx

# Délai de garde par défaut ; le téléchargement ne doit pas bloquer indéfiniment
# une tâche Celery. Surchargé par ``timeout`` si besoin.
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_USER_AGENT = "PARTIPOLAI-Ingestion/1.0"


class Downloader:
    """Télécharge le contenu brut d'une URL (Exigence 11.1).

    Sans état : une instance peut être réutilisée. Les erreurs réseau/HTTP sont
    propagées (``httpx.HTTPError``) pour être gérées par la politique de réessai
    de la tâche Celery orchestrante (Exigence 23).
    """

    def __init__(
        self,
        *,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        user_agent: str = _DEFAULT_USER_AGENT,
    ) -> None:
        self._timeout = timeout
        self._user_agent = user_agent

    def fetch(self, url: str) -> "RawDocument":
        """Récupère ``url`` et renvoie un :class:`RawDocument`.

        Suit les redirections et lève une exception pour les statuts HTTP ≥ 400
        (``raise_for_status``), afin que l'échec remonte à l'orchestrateur.
        """
        # Import différé pour éviter tout couplage à l'initialisation du paquet.
        from app.rag.ingestion.types import RawDocument

        headers = {"User-Agent": self._user_agent}
        with httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers=headers,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type")
            return RawDocument(
                url=str(response.url),
                content=response.content,
                content_type=content_type,
                headers={
                    key.lower(): value for key, value in response.headers.items()
                },
            )


# Réexport pour l'annotation de retour ci-dessus sans import circulaire à l'exécution.
from app.rag.ingestion.types import RawDocument  # noqa: E402

__all__ = ["Downloader"]
