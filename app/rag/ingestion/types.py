"""Types partagés du pipeline d'ingestion (Exigence 11).

Regroupe les structures de données échangées entre les étapes du pipeline afin
d'éviter les imports circulaires entre modules (``downloader`` produit un
:class:`RawDocument`, consommé par ``format_detector`` puis ``extractor``).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Contenu brut récupéré pour une URL, avant toute extraction.

    Attributs :
    * ``url`` — URL source (sert d'indice de format via son extension).
    * ``content`` — octets bruts tels que téléchargés.
    * ``content_type`` — valeur d'en-tête ``Content-Type`` si connue (indice de
      format prioritaire sur l'extension).
    * ``headers`` — en-têtes de réponse pertinents (facultatif).

    Immuable (``frozen``) : les étapes du pipeline ne mutent jamais l'entrée,
    elles produisent de nouvelles valeurs — utile pour l'idempotence.
    """

    url: str
    content: bytes
    content_type: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


__all__ = ["RawDocument"]
