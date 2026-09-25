"""Schémas Pydantic v2 pour le vote (Exigence 6, Exigence 7).

* :class:`VoteRequest` — corps de ``POST /api/v1/proposals/{id}/votes`` ;
  ``value ∈ {+1, 0, -1}`` (Exigence 6.1).
* :class:`VoteCounts` — décomptes agrégés des Votes d'une Proposition, renvoyés
  par ``VoteService`` (``cast`` / ``withdraw`` / ``counts``) et exposés par
  ``GET /api/v1/proposals/{id}/votes`` (Exigences 6.4, 6.5, 7.1).

``VoteCounts`` fournit toujours les décomptes **absolus** (soutien, opposition,
participation), conformément au principe « décomptes explicites » (Exigence 7.4).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# Valeurs de vote autorisées : soutien (+1), neutre (0), opposition (-1).
VoteValue = Literal[-1, 0, 1]


class VoteRequest(BaseModel):
    """Corps de ``POST /api/v1/proposals/{id}/votes`` (Exigence 6.1)."""

    value: VoteValue = Field(
        description="Valeur du vote : +1 (soutien), 0 (neutre) ou -1 (opposition).",
    )


class VoteCounts(BaseModel):
    """Décomptes agrégés des Votes d'une Proposition (Exigences 6.5, 7.1).

    * ``support_count`` — nombre de Votes ``+1`` ;
    * ``oppose_count`` — nombre de Votes ``-1`` ;
    * ``neutral_count`` — nombre de Votes ``0`` ;
    * ``participation_count`` — nombre total de Votes exprimés (toutes valeurs).
    """

    proposal_id: int
    support_count: int = Field(default=0, ge=0)
    oppose_count: int = Field(default=0, ge=0)
    neutral_count: int = Field(default=0, ge=0)
    participation_count: int = Field(default=0, ge=0)
