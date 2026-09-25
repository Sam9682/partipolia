"""Schémas Pydantic v2 pour les fonctions RGPD (Exigence 28.4).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.privacy_service.PrivacyService` et de l'API RGPD
(``app/api/v1/privacy.py``) :

* :class:`ConsentUpdate` / :class:`ConsentState` — lecture et mise à jour de la
  **gestion du consentement** (Exigence 28.4) ;
* :class:`DataExport` — **export des données** personnelles d'un compte, incluant
  ses Propositions, Votes, Arguments et Commentaires (Exigence 28.4) ;
* :class:`AccountDeletionResult` — récapitulatif de la **suppression de compte**
  (Exigence 28.4) ;
* :class:`RetentionPolicy` — **politique de rétention explicite** exposée aux
  Utilisateurs (comptes, Votes, journaux, conversations IA, signalements ;
  Exigence 28.5).

Aucun de ces schémas ne collecte ni n'expose de donnée de profilage politique
individuel (Exigences 28.1, 28.2) : l'export ne restitue que des données déjà
fournies ou produites par l'Utilisateur lui-même.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConsentUpdate(BaseModel):
    """Corps de mise à jour du consentement (Exigence 28.4).

    ``consents`` est un dictionnaire ``{clé: bool}`` de choix de consentement
    fonctionnels. Toute valeur non booléenne est rejetée par la validation
    Pydantic ; aucune donnée de profilage n'est acceptée (Exigences 28.1, 28.2).
    """

    consents: dict[str, bool] = Field(
        default_factory=dict,
        description="Choix de consentement fonctionnels ({clé: bool}).",
    )


class ConsentState(BaseModel):
    """État courant du consentement d'un Utilisateur (Exigence 28.4)."""

    consents: dict[str, bool] = Field(default_factory=dict)


class ExportedProposal(BaseModel):
    """Proposition rédigée par l'Utilisateur, incluse à l'export (Exigence 28.4)."""

    id: int
    slug: str
    title: str
    status: str
    version: int
    created_at: datetime


class ExportedVote(BaseModel):
    """Vote émis par l'Utilisateur, inclus à l'export (Exigence 28.4)."""

    proposal_id: int
    value: int
    created_at: datetime


class ExportedArgument(BaseModel):
    """Argument rédigé par l'Utilisateur, inclus à l'export (Exigence 28.4)."""

    id: int
    proposal_id: int
    position: str
    content: str
    created_at: datetime


class ExportedComment(BaseModel):
    """Commentaire rédigé par l'Utilisateur, inclus à l'export (Exigence 28.4)."""

    id: int
    proposal_id: int
    parent_id: int | None
    content: str
    status: str
    created_at: datetime


class ExportedAccount(BaseModel):
    """Données du compte incluses à l'export — jamais ``password_hash`` (Exigence 28.4)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    display_name: str
    is_active: bool
    is_verified: bool
    is_admin: bool
    consents: dict[str, bool] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class DataExport(BaseModel):
    """Export complet et portable des données d'un Utilisateur (Exigence 28.4).

    Le mot de passe (haché ou non) n'est jamais exporté (Exigences 1.3, 1.8) ; la
    Plateforme ne restitue que des données fournies ou produites par
    l'Utilisateur, sans profil politique reconstitué (Exigences 28.1, 28.2).
    """

    generated_at: datetime
    account: ExportedAccount
    proposals: list[ExportedProposal] = Field(default_factory=list)
    votes: list[ExportedVote] = Field(default_factory=list)
    arguments: list[ExportedArgument] = Field(default_factory=list)
    comments: list[ExportedComment] = Field(default_factory=list)


class AccountDeletionResult(BaseModel):
    """Récapitulatif de la suppression de compte (Exigence 28.4).

    Détaille les données supprimées (Votes, Arguments, Commentaires, compte) et
    les Propositions **anonymisées** plutôt que supprimées : les Propositions
    publiées relèvent de l'intérêt collectif de la Plateforme et sont conservées
    en dissociant l'identité de leur auteur.
    """

    deleted: bool
    anonymized_proposals: int = Field(ge=0)
    deleted_votes: int = Field(ge=0)
    deleted_arguments: int = Field(ge=0)
    deleted_comments: int = Field(ge=0)


class RetentionRule(BaseModel):
    """Règle de rétention d'une catégorie de données (Exigence 28.5)."""

    category: str
    retention: str
    basis: str


class RetentionPolicy(BaseModel):
    """Politique de rétention explicite de la Plateforme (Exigence 28.5).

    Couvre les comptes, les Votes, les journaux, les conversations IA et les
    signalements, avec pour chaque catégorie une durée/critère de conservation et
    la base de la conservation.
    """

    rules: list[RetentionRule] = Field(default_factory=list)
