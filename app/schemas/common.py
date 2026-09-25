"""Schémas Pydantic v2 partagés par l'API (pagination).

Ce module fournit :class:`Page`, une enveloppe de pagination générique réutilisée
par les points d'accès listant des collections (Thèmes → Propositions, liste des
Propositions, etc.). Elle expose les éléments de la page courante ainsi que les
métadonnées de pagination (``total``, ``page``, ``limit``, ``pages``) nécessaires
au rendu côté client comme au rendu SSR.
"""

from __future__ import annotations

from math import ceil
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Enveloppe de pagination générique.

    * ``items`` — éléments de la page courante ;
    * ``total`` — nombre total d'éléments correspondant au filtre ;
    * ``page`` — index de page (1-based) ;
    * ``limit`` — taille de page demandée ;
    * ``pages`` — nombre total de pages (au moins 1).
    """

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
    pages: int = Field(ge=0)

    @classmethod
    def create(cls, items: list[T], *, total: int, page: int, limit: int) -> "Page[T]":
        """Construit une :class:`Page` en calculant le nombre de pages.

        ``pages`` vaut 0 lorsqu'aucun élément ne correspond, sinon ``ceil(total /
        limit)``, ce qui garantit ``page ≤ pages`` pour toute page non vide.
        """
        pages = ceil(total / limit) if total > 0 else 0
        return cls(items=items, total=total, page=page, limit=limit, pages=pages)
