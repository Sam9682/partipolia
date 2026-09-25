"""Service d'authentification (Exigence 1, Exigence 26.8).

Ce Service implémente le contrat ``AuthService`` de la conception :

* ``register`` — création de compte avec email unique et mot de passe haché
  (Exigence 1.1) ; en cas d'email déjà enregistré, une erreur **générique** est
  levée sans révéler l'existence du compte (Exigence 1.2) ;
* ``authenticate`` — vérification des identifiants et émission d'un couple JWT
  access + refresh (Exigence 1.4) ; identifiants invalides ⇒ erreur **générique**
  (Exigence 1.9) ;
* ``refresh`` — émission d'un nouvel access token à partir d'un refresh token
  valide et non révoqué (Exigence 1.6) ;
* ``logout`` — invalidation de la session courante via une liste de révocation
  Redis (Exigence 1.7) ;
* ``current_user`` — données du compte courant **sans** ``password_hash``
  (Exigence 1.8) ;
* ``hash_password`` / ``verify_password`` — hachage argon2 avec repli bcrypt,
  seul ``password_hash`` étant stocké (Exigence 1.3).

Principes de sécurité :

* Le mot de passe n'est jamais journalisé ni renvoyé.
* Les messages d'erreur d'inscription et de connexion sont volontairement
  génériques pour ne pas divulguer si un email existe (Exigences 1.2, 1.9).
* Pour contrer les attaques temporelles à la connexion, une vérification
  factice est effectuée lorsque l'email est inconnu.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Final

from argon2 import PasswordHasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)
from jose import JWTError, jwt
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, settings as default_settings
from app.core.redis import get_redis
from app.models.user import User
from app.schemas.auth import AccessToken, TokenPair, UserPublic

# --- Messages d'erreur génériques (Exigences 1.2, 1.9) ---------------------
# Volontairement identiques et neutres : ils ne révèlent jamais si un email
# existe déjà ni lequel des deux champs (email / mot de passe) est en cause.
GENERIC_REGISTRATION_ERROR: Final = "Inscription impossible avec ces informations."
GENERIC_CREDENTIALS_ERROR: Final = "Identifiants invalides."
INVALID_TOKEN_ERROR: Final = "Jeton invalide ou expiré."

# --- Types de jetons JWT (claim ``type``) ----------------------------------
_TOKEN_TYPE_ACCESS: Final = "access"
_TOKEN_TYPE_REFRESH: Final = "refresh"

# Préfixe des clés Redis de révocation de session/refresh (Exigence 1.7).
_DENYLIST_KEY_PREFIX: Final = "auth:denylist:"

# Hachage bcrypt : préfixes reconnus pour router la vérification vers bcrypt.
_BCRYPT_PREFIXES: Final = ("$2a$", "$2b$", "$2y$")


class AuthError(Exception):
    """Erreur d'authentification à message générique (Exigences 1.2, 1.9).

    Elle ne contient jamais d'information permettant de distinguer les cas
    (email déjà pris, mot de passe erroné, compte inexistant, etc.).
    """


class AuthService:
    """Logique d'authentification : hachage, JWT, sessions.

    Le Service est instancié par requête avec une ``AsyncSession`` ; le client
    Redis et la configuration sont injectables pour faciliter les tests.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        redis: Redis | None = None,
        config: Settings | None = None,
    ) -> None:
        self._session = session
        self._redis = redis if redis is not None else get_redis()
        self._settings = config if config is not None else default_settings
        # Hacheur argon2 avec paramètres par défaut robustes de argon2-cffi.
        self._hasher = PasswordHasher()

    # ------------------------------------------------------------------ #
    # Hachage de mot de passe (Exigence 1.3)                             #
    # ------------------------------------------------------------------ #
    def hash_password(self, plain: str) -> str:
        """Hache un mot de passe avec argon2 (repli bcrypt si argon2 indisponible).

        Seul le résultat (``password_hash``) est destiné à être stocké ; le mot
        de passe en clair n'est jamais persisté (Exigence 1.3).
        """
        try:
            return self._hasher.hash(plain)
        except Exception:  # pragma: no cover - repli défensif si argon2 échoue
            return self._bcrypt_hash(plain)

    def verify_password(self, plain: str, hashed: str) -> bool:
        """Vérifie un mot de passe contre son hachage (argon2 ou bcrypt).

        Le format du hachage détermine l'algorithme de vérification, ce qui
        permet un repli et une migration transparente entre bcrypt et argon2.
        """
        if hashed.startswith(_BCRYPT_PREFIXES):
            return self._bcrypt_verify(plain, hashed)
        try:
            return self._hasher.verify(hashed, plain)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False

    @staticmethod
    def _bcrypt_hash(plain: str) -> str:
        import bcrypt

        # bcrypt limite l'entrée à 72 octets ; on tronque l'encodage UTF-8.
        digest = bcrypt.hashpw(plain.encode("utf-8")[:72], bcrypt.gensalt())
        return digest.decode("utf-8")

    @staticmethod
    def _bcrypt_verify(plain: str, hashed: str) -> bool:
        import bcrypt

        try:
            return bcrypt.checkpw(plain.encode("utf-8")[:72], hashed.encode("utf-8"))
        except ValueError:
            return False

    # ------------------------------------------------------------------ #
    # Inscription (Exigences 1.1, 1.2, 1.3)                              #
    # ------------------------------------------------------------------ #
    async def register(self, email: str, password: str, display_name: str) -> User:
        """Crée un Utilisateur ; email déjà pris ⇒ erreur générique (Exigences 1.1, 1.2)."""
        normalized_email = self._normalize_email(email)

        # Contrôle applicatif d'unicité (Exigence 1.1) — le message reste générique.
        existing = await self._session.scalar(
            select(User).where(User.email == normalized_email)
        )
        if existing is not None:
            # Ne révèle pas que l'email est déjà enregistré (Exigence 1.2).
            raise AuthError(GENERIC_REGISTRATION_ERROR)

        user = User(
            email=normalized_email,
            password_hash=self.hash_password(password),
            display_name=display_name,
            is_active=True,
            is_verified=False,
            is_admin=False,
        )
        self._session.add(user)
        try:
            # Flush pour matérialiser l'id et déclencher la contrainte UNIQUE, qui
            # protège des courses concurrentes que le SELECT ci-dessus ne couvre pas.
            await self._session.flush()
        except IntegrityError as exc:
            await self._session.rollback()
            raise AuthError(GENERIC_REGISTRATION_ERROR) from exc
        return user

    # ------------------------------------------------------------------ #
    # Connexion (Exigences 1.4, 1.9)                                     #
    # ------------------------------------------------------------------ #
    async def authenticate(self, email: str, password: str) -> TokenPair:
        """Vérifie les identifiants et émet un couple access + refresh (Exigence 1.4).

        Identifiants invalides ⇒ ``AuthError`` générique (Exigence 1.9). Une
        vérification factice est réalisée si l'email est inconnu afin d'égaliser
        les temps de réponse (protection contre l'énumération temporelle).
        """
        normalized_email = self._normalize_email(email)
        user = await self._session.scalar(
            select(User).where(User.email == normalized_email)
        )

        if user is None:
            # Vérification factice pour un temps de réponse comparable au cas nominal.
            self._dummy_verify()
            raise AuthError(GENERIC_CREDENTIALS_ERROR)

        if not user.is_active or not self.verify_password(password, user.password_hash):
            raise AuthError(GENERIC_CREDENTIALS_ERROR)

        return self._issue_token_pair(user)

    def _dummy_verify(self) -> None:
        """Vérifie un hachage factice pour uniformiser les temps de réponse."""
        try:
            self._hasher.verify(
                self._hasher.hash("timing-attack-mitigation"),
                "invalid-password",
            )
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            pass

    # ------------------------------------------------------------------ #
    # Rafraîchissement (Exigence 1.6)                                    #
    # ------------------------------------------------------------------ #
    async def refresh(self, refresh_token: str) -> AccessToken:
        """Émet un nouvel access token à partir d'un refresh token valide (Exigence 1.6)."""
        payload = self._decode_token(refresh_token, expected_type=_TOKEN_TYPE_REFRESH)

        # Un refresh token révoqué (logout) ne peut plus produire de jeton (Exigence 1.7).
        if await self._is_revoked(payload.get("jti", "")):
            raise AuthError(INVALID_TOKEN_ERROR)

        subject = payload.get("sub")
        if subject is None:
            raise AuthError(INVALID_TOKEN_ERROR)

        user = await self._session.get(User, int(subject))
        if user is None or not user.is_active:
            raise AuthError(INVALID_TOKEN_ERROR)

        access_token, expires_in = self._create_access_token(user)
        return AccessToken(access_token=access_token, expires_in=expires_in)

    # ------------------------------------------------------------------ #
    # Déconnexion (Exigence 1.7)                                         #
    # ------------------------------------------------------------------ #
    async def logout(self, session_id: str) -> None:
        """Invalide la session courante en révoquant son identifiant (Exigence 1.7).

        ``session_id`` est le claim ``jti`` du jeton (access ou refresh). Il est
        ajouté à une liste de révocation Redis avec une expiration alignée sur la
        durée de vie maximale d'un refresh token, ce qui évite une croissance
        illimitée des clés.
        """
        if not session_id:
            return
        ttl_seconds = self._settings.jwt_refresh_token_expire_days * 24 * 60 * 60
        await self._redis.set(f"{_DENYLIST_KEY_PREFIX}{session_id}", "1", ex=ttl_seconds)

    async def _is_revoked(self, jti: str) -> bool:
        """Indique si un ``jti`` figure dans la liste de révocation Redis."""
        if not jti:
            return False
        return bool(await self._redis.exists(f"{_DENYLIST_KEY_PREFIX}{jti}"))

    # ------------------------------------------------------------------ #
    # Compte courant (Exigence 1.8)                                      #
    # ------------------------------------------------------------------ #
    async def current_user(self, access_token: str) -> UserPublic:
        """Retourne les données publiques du compte courant, sans ``password_hash`` (Exigence 1.8)."""
        payload = self._decode_token(access_token, expected_type=_TOKEN_TYPE_ACCESS)

        if await self._is_revoked(payload.get("jti", "")):
            raise AuthError(INVALID_TOKEN_ERROR)

        subject = payload.get("sub")
        if subject is None:
            raise AuthError(INVALID_TOKEN_ERROR)

        user = await self._session.get(User, int(subject))
        if user is None or not user.is_active:
            raise AuthError(INVALID_TOKEN_ERROR)

        # ``UserPublic`` ne comporte pas de champ ``password_hash`` (Exigence 1.8).
        return UserPublic.model_validate(user)

    # ------------------------------------------------------------------ #
    # Émission et décodage des JWT                                       #
    # ------------------------------------------------------------------ #
    def _issue_token_pair(self, user: User) -> TokenPair:
        """Construit le couple access + refresh pour un Utilisateur (Exigence 1.4)."""
        access_token, expires_in = self._create_access_token(user)
        refresh_token = self._create_refresh_token(user)
        return TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_in=expires_in,
        )

    def _create_access_token(self, user: User) -> tuple[str, int]:
        """Crée un access token JWT signé et retourne (jeton, durée de vie en secondes)."""
        expire_minutes = self._settings.jwt_access_token_expire_minutes
        expires_in = expire_minutes * 60
        token = self._encode_token(user, _TOKEN_TYPE_ACCESS, timedelta(minutes=expire_minutes))
        return token, expires_in

    def _create_refresh_token(self, user: User) -> str:
        """Crée un refresh token JWT signé (Exigence 1.4)."""
        expire_days = self._settings.jwt_refresh_token_expire_days
        return self._encode_token(user, _TOKEN_TYPE_REFRESH, timedelta(days=expire_days))

    def _encode_token(self, user: User, token_type: str, lifetime: timedelta) -> str:
        """Encode un JWT avec ``sub``, ``type``, ``jti``, ``iat`` et ``exp``."""
        now = datetime.now(timezone.utc)
        claims: dict[str, Any] = {
            "sub": str(user.id),
            "type": token_type,
            "jti": uuid.uuid4().hex,
            "iat": int(now.timestamp()),
            "exp": now + lifetime,
        }
        return jwt.encode(
            claims,
            self._settings.jwt_secret_key.get_secret_value(),
            algorithm=self._settings.jwt_algorithm,
        )

    def _decode_token(self, token: str, *, expected_type: str) -> dict[str, Any]:
        """Décode et valide un JWT ; type inattendu ou signature invalide ⇒ erreur générique."""
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                self._settings.jwt_secret_key.get_secret_value(),
                algorithms=[self._settings.jwt_algorithm],
            )
        except JWTError as exc:
            raise AuthError(INVALID_TOKEN_ERROR) from exc

        if payload.get("type") != expected_type:
            raise AuthError(INVALID_TOKEN_ERROR)
        return payload

    # ------------------------------------------------------------------ #
    # Utilitaires                                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize_email(email: str) -> str:
        """Normalise un email (minuscules, espaces retirés) pour l'unicité (Exigence 1.1)."""
        return email.strip().lower()
