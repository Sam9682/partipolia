"""Schémas Pydantic v2 pour les Propositions (Exigences 3 et 4).

Ces schémas définissent les contrats d'entrée/sortie de ``ProposalService`` et
de l'API des Propositions (``app/api/v1/proposals.py``, tâche 4.5) :

* :class:`ProposalCreate` — corps de ``POST /api/v1/proposals`` (Exigence 4.1) ;
  validation Pydantic des champs obligatoires (Exigence 4.2) ;
* :class:`ProposalUpdate` — corps de ``PUT /api/v1/proposals/{id}`` ; tous les
  champs sont optionnels (mise à jour partielle) et un ``change_summary`` décrit
  la modification consignée dans l'historique (Exigences 3.4, 4.5) ;
* :class:`ProposalSummary` — représentation condensée pour la liste
  (``GET /api/v1/proposals``, Exigence 4.3) ;
* :class:`ProposalDetail` — représentation détaillée d'une Proposition
  (``GET /api/v1/proposals/{id}``, Exigence 4.4) ;
* :class:`ProposalVersionInfo` — entrée d'historique de version (Exigence 3.4) ;
* :class:`Page` — enveloppe de pagination générique renvoyée par ``list``.

Les valeurs de tri (:data:`ProposalSort`) excluent volontairement tout tri par
nombre de votes : le tri ne s'appuie jamais sur ``ORDER BY vote_count``
(Exigence 7.3).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

# Valeurs de tri autorisées pour la liste des Propositions (Exigence 4.3).
# Aucune de ces valeurs ne trie par nombre de votes (Exigence 7.3).
ProposalSort = Literal["recent", "oldest", "title", "updated"]

# Ensemble des valeurs de tri reconnues, pour validation côté Service.
PROPOSAL_SORTS: frozenset[str] = frozenset({"recent", "oldest", "title", "updated"})

T = TypeVar("T")


class ProposalCreate(BaseModel):
    """Corps de ``POST /api/v1/proposals`` (Exigences 4.1, 4.2).

    Les champs obligatoires (``theme_id``, ``title``, ``problem``,
    ``description``) sont validés par Pydantic ; leur absence entraîne une erreur
    de validation décrivant les champs en cause (Exigence 4.2).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    theme_id: int = Field(gt=0)
    title: str = Field(min_length=3, max_length=255)
    problem: str = Field(min_length=1)
    description: str = Field(min_length=1)
    expected_impact: str | None = None
    implementation_delay: str | None = Field(default=None, max_length=120)
    estimated_cost: Decimal | None = None
    estimated_savings: Decimal | None = None
    estimated_revenue: Decimal | None = None
    funding_description: str | None = None
    legal_constraints: str | None = None


class ProposalUpdate(BaseModel):
    """Corps de ``PUT /api/v1/proposals/{id}`` (Exigences 3.4, 4.5).

    Tous les champs métier sont optionnels (mise à jour partielle) ; seuls les
    champs fournis (``exclude_unset``) sont appliqués. ``change_summary`` décrit
    la modification et est consigné dans la ``ProposalVersion`` créée.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=3, max_length=255)
    problem: str | None = Field(default=None, min_length=1)
    description: str | None = Field(default=None, min_length=1)
    expected_impact: str | None = None
    implementation_delay: str | None = Field(default=None, max_length=120)
    estimated_cost: Decimal | None = None
    estimated_savings: Decimal | None = None
    estimated_revenue: Decimal | None = None
    funding_description: str | None = None
    legal_constraints: str | None = None
    theme_id: int | None = Field(default=None, gt=0)
    change_summary: str | None = Field(default=None, max_length=2000)


class ProposalVersionInfo(BaseModel):
    """Entrée d'historique d'une Proposition (Exigences 3.4, 3.5)."""

    model_config = ConfigDict(from_attributes=True)

    version: int
    change_summary: str | None = None
    edited_by: int | None = None
    created_at: datetime


class ProposalSummary(BaseModel):
    """Représentation condensée d'une Proposition pour la liste (Exigence 4.3)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    theme_id: int
    author_id: int
    status: str
    version: int
    created_at: datetime
    updated_at: datetime


class ProposalDetail(BaseModel):
    """Représentation détaillée d'une Proposition (Exigence 4.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    title: str
    theme_id: int
    author_id: int
    problem: str
    description: str
    expected_impact: str | None = None
    implementation_delay: str | None = None
    estimated_cost: Decimal | None = None
    estimated_savings: Decimal | None = None
    estimated_revenue: Decimal | None = None
    funding_description: str | None = None
    legal_constraints: str | None = None
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    versions: list[ProposalVersionInfo] = Field(default_factory=list)


class Page(BaseModel, Generic[T]):
    """Enveloppe de pagination générique renvoyée par ``ProposalService.list``.

    * ``items`` — éléments de la page courante ;
    * ``total`` — nombre total d'éléments correspondant au filtre ;
    * ``page`` / ``limit`` — pagination demandée.
    """

    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    limit: int = Field(ge=1)
