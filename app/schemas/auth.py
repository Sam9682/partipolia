"""Schémas Pydantic v2 pour l'authentification (Exigence 1).

Ces schémas définissent les contrats d'entrée/sortie de l'``AuthService`` et de
l'API d'authentification (``app/api/v1/auth.py``, tâche 3.3) :

* :class:`RegisterRequest` / :class:`LoginRequest` — corps des requêtes ;
* :class:`TokenPair` — couple access + refresh token émis à la connexion
  (Exigence 1.4) ;
* :class:`AccessToken` — nouveau access token émis au rafraîchissement
  (Exigence 1.6) ;
* :class:`UserPublic` — représentation publique du compte, **sans**
  ``password_hash`` (Exigence 1.8).

Aucun de ces schémas n'expose ``password_hash`` : le mot de passe n'est jamais
renvoyé ni sérialisé (Exigences 1.3, 1.8).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# Type de jeton porteur normalisé (OAuth2 « bearer »).
TOKEN_TYPE_BEARER = "bearer"


class RegisterRequest(BaseModel):
    """Corps de ``POST /api/v1/auth/register`` (Exigence 1.1)."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(min_length=1, max_length=120)


class LoginRequest(BaseModel):
    """Corps de ``POST /api/v1/auth/login`` (Exigence 1.4)."""

    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class AccessToken(BaseModel):
    """Access token seul, émis au rafraîchissement (Exigence 1.6)."""

    access_token: str
    token_type: str = TOKEN_TYPE_BEARER
    expires_in: int = Field(description="Durée de vie de l'access token en secondes.")


class TokenPair(BaseModel):
    """Couple access + refresh token émis à la connexion (Exigence 1.4)."""

    access_token: str
    refresh_token: str
    token_type: str = TOKEN_TYPE_BEARER
    expires_in: int = Field(description="Durée de vie de l'access token en secondes.")


class UserPublic(BaseModel):
    """Représentation publique d'un Utilisateur — jamais ``password_hash`` (Exigence 1.8)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    display_name: str
    is_active: bool
    is_verified: bool
    is_admin: bool
    created_at: datetime
    updated_at: datetime
