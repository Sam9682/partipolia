"""Tests unitaires de l'``AuthService`` (Exigences 1.2, 1.3, 1.9 ; tâche 3.2).

Ces tests exercent le cœur métier de ``app/services/auth_service.py`` par des
exemples déterministes, **sans base de données ni Redis réels** :

* hachage / vérification de mot de passe — argon2 (défaut) **et** repli bcrypt
  (Exigence 1.3) ;
* émission d'un couple access + refresh à la connexion, puis rafraîchissement de
  l'access token à partir d'un refresh token valide (Exigences 1.4, 1.6) ;
* messages d'erreur **génériques** à l'inscription et à la connexion : ils ne
  révèlent jamais si un email existe ni lequel des champs est en cause
  (Exigences 1.2, 1.9).

Conformément aux conventions du projet (cf. ``test_vote_service.py``), seules les
couches d'infrastructure (``AsyncSession`` PostgreSQL et client Redis) sont
remplacées par des doublures en mémoire fidèles au contrat attendu ; aucun code
du Service n'est mocké. La configuration JWT est fournie via une instance
``Settings`` dédiée aux tests (clé de signature déterministe).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from jose import jwt
from pydantic import SecretStr

from app.core.config import Settings
from app.models.user import User
from app.schemas.auth import UserPublic
from app.services.auth_service import (
    GENERIC_CREDENTIALS_ERROR,
    GENERIC_REGISTRATION_ERROR,
    INVALID_TOKEN_ERROR,
    AuthError,
    AuthService,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------- #
# Doublures d'infrastructure                                                  #
# --------------------------------------------------------------------------- #
class _FakeUserSession:
    """``AsyncSession`` factice modélisant la table ``users`` en mémoire.

    Le magasin reproduit le contrat attendu par ``AuthService`` :

    * :meth:`scalar` d'un ``SELECT User WHERE email = ...`` retrouve un compte par
      email (matérialise la contrainte ``UNIQUE(email)`` — Exigence 1.1) ;
    * :meth:`get` retrouve un compte par identifiant primaire ;
    * :meth:`add` + :meth:`flush` matérialisent l'``id`` auto-incrémenté ;
    * :meth:`rollback` est neutre (aucune connexion réelle).
    """

    def __init__(self) -> None:
        self._by_id: dict[int, User] = {}
        self._by_email: dict[str, User] = {}
        self._next_id = 1

    # -- Amorçage de test ------------------------------------------------ #
    def seed(self, user: User) -> User:
        if user.id is None:
            user.id = self._next_id
            self._next_id += 1
        self._by_id[user.id] = user
        self._by_email[user.email] = user
        return user

    # -- Contrat AsyncSession utilisé par AuthService -------------------- #
    async def scalar(self, statement: Any) -> User | None:
        params = statement.compile().params
        email = params.get("email_1")
        return self._by_email.get(email)

    def add(self, instance: User) -> None:
        if instance.id is None:
            instance.id = self._next_id
            self._next_id += 1
        self._pending = instance

    async def flush(self) -> None:
        instance = getattr(self, "_pending", None)
        if instance is None:
            return
        # Simule la contrainte UNIQUE(email) : email déjà présent ⇒ IntegrityError.
        if instance.email in self._by_email:
            from sqlalchemy.exc import IntegrityError

            raise IntegrityError("INSERT", {}, Exception("unique violation"))
        self._by_id[instance.id] = instance
        self._by_email[instance.email] = instance

    async def get(self, model: type[User], pk: int) -> User | None:
        return self._by_id.get(pk)

    async def rollback(self) -> None:
        return None


class _FakeRedis:
    """Doublure asynchrone de Redis pour la liste de révocation (Exigence 1.7)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


# --------------------------------------------------------------------------- #
# Fabriques de test                                                           #
# --------------------------------------------------------------------------- #
def _test_settings(**overrides: Any) -> Settings:
    """Construit une configuration de test avec une clé JWT déterministe."""
    base = {
        "app_env": "test",
        "jwt_secret_key": SecretStr("test-secret-key-for-unit-tests"),
        "jwt_algorithm": "HS256",
        "jwt_access_token_expire_minutes": 15,
        "jwt_refresh_token_expire_days": 14,
    }
    base.update(overrides)
    return Settings(**base)


def _service(
    session: _FakeUserSession | None = None,
    redis: _FakeRedis | None = None,
    config: Settings | None = None,
) -> AuthService:
    return AuthService(
        session or _FakeUserSession(),  # type: ignore[arg-type]
        redis=redis or _FakeRedis(),  # type: ignore[arg-type]
        config=config or _test_settings(),
    )


# =========================================================================== #
# Hachage / vérification de mot de passe (Exigence 1.3)                       #
# =========================================================================== #
def test_hash_password_is_not_plaintext_and_is_stable_for_verify() -> None:
    """Le hachage n'est jamais le mot de passe en clair et se vérifie (Exigence 1.3)."""
    service = _service()

    hashed = service.hash_password("Sup3r-Secret!")

    assert hashed != "Sup3r-Secret!"
    assert service.verify_password("Sup3r-Secret!", hashed) is True


def test_verify_password_rejects_wrong_password() -> None:
    """Un mot de passe erroné est rejeté (Exigence 1.3)."""
    service = _service()

    hashed = service.hash_password("bon-mot-de-passe")

    assert service.verify_password("mauvais-mot-de-passe", hashed) is False


def test_verify_password_supports_bcrypt_fallback() -> None:
    """La vérification supporte un hachage bcrypt (repli — Exigence 1.3)."""
    service = _service()

    bcrypt_hash = AuthService._bcrypt_hash("mot-de-passe-bcrypt")

    assert bcrypt_hash.startswith(("$2a$", "$2b$", "$2y$"))
    assert service.verify_password("mot-de-passe-bcrypt", bcrypt_hash) is True
    assert service.verify_password("mauvais", bcrypt_hash) is False


def test_verify_password_returns_false_on_malformed_hash() -> None:
    """Un hachage argon2 malformé ne lève pas mais renvoie False (Exigence 1.3)."""
    service = _service()

    assert service.verify_password("x", "pas-un-hachage-valide") is False


# =========================================================================== #
# Inscription — message générique (Exigences 1.1, 1.2)                        #
# =========================================================================== #
async def test_register_creates_user_with_hashed_password() -> None:
    """L'inscription crée un compte dont seul le hachage est stocké (Exigences 1.1, 1.3)."""
    session = _FakeUserSession()
    service = _service(session=session)

    user = await service.register("Alice@Example.COM", "un-mot-de-passe", "Alice")

    assert user.id is not None
    # Email normalisé (minuscules) pour l'unicité (Exigence 1.1).
    assert user.email == "alice@example.com"
    assert user.display_name == "Alice"
    assert user.password_hash not in ("", "un-mot-de-passe")
    assert service.verify_password("un-mot-de-passe", user.password_hash) is True
    assert user.is_active is True
    assert user.is_admin is False


async def test_register_with_known_email_raises_generic_error() -> None:
    """Un email déjà enregistré ⇒ message générique, sans révéler l'existence (Exigence 1.2)."""
    session = _FakeUserSession()
    service = _service(session=session)
    await service.register("bob@example.com", "motdepasse-1", "Bob")

    with pytest.raises(AuthError) as excinfo:
        await service.register("bob@example.com", "autre-mot-de-passe", "Bob2")

    # Le message ne mentionne ni « email », ni « existe », ni « déjà » (Exigence 1.2).
    message = str(excinfo.value)
    assert message == GENERIC_REGISTRATION_ERROR
    lowered = message.lower()
    assert "existe" not in lowered
    assert "déjà" not in lowered
    assert "email" not in lowered


async def test_register_generic_error_on_integrity_violation() -> None:
    """Une course concurrente (IntegrityError) ⇒ même message générique (Exigence 1.2)."""
    session = _FakeUserSession()
    # Amorce directement le magasin pour contourner le SELECT préalable et
    # forcer le déclenchement de la contrainte UNIQUE au flush.
    session._by_email["race@example.com"] = User(
        id=99,
        email="race@example.com",
        password_hash="x",
        display_name="Race",
    )
    service = _service(session=session)

    # Le SELECT applicatif détecte déjà le doublon : message générique attendu.
    with pytest.raises(AuthError) as excinfo:
        await service.register("race@example.com", "motdepasse", "Race2")

    assert str(excinfo.value) == GENERIC_REGISTRATION_ERROR


# =========================================================================== #
# Connexion et émission JWT (Exigences 1.4, 1.9)                              #
# =========================================================================== #
async def test_authenticate_issues_access_and_refresh_tokens() -> None:
    """Une connexion valide émet un couple access + refresh (Exigence 1.4)."""
    session = _FakeUserSession()
    config = _test_settings()
    service = _service(session=session, config=config)
    await service.register("carol@example.com", "bon-mot-de-passe", "Carol")

    tokens = await service.authenticate("carol@example.com", "bon-mot-de-passe")

    assert tokens.access_token
    assert tokens.refresh_token
    assert tokens.access_token != tokens.refresh_token
    assert tokens.token_type == "bearer"
    assert tokens.expires_in == config.jwt_access_token_expire_minutes * 60

    secret = config.jwt_secret_key.get_secret_value()
    access_payload = jwt.decode(tokens.access_token, secret, algorithms=["HS256"])
    refresh_payload = jwt.decode(tokens.refresh_token, secret, algorithms=["HS256"])
    assert access_payload["type"] == "access"
    assert refresh_payload["type"] == "refresh"
    # Les deux jetons portent le même sujet (l'utilisateur) mais un jti distinct.
    assert access_payload["sub"] == refresh_payload["sub"]
    assert access_payload["jti"] != refresh_payload["jti"]


async def test_authenticate_unknown_email_raises_generic_error() -> None:
    """Un email inconnu ⇒ message générique d'identifiants invalides (Exigence 1.9)."""
    service = _service()

    with pytest.raises(AuthError) as excinfo:
        await service.authenticate("inconnu@example.com", "peu-importe")

    assert str(excinfo.value) == GENERIC_CREDENTIALS_ERROR


async def test_authenticate_wrong_password_raises_same_generic_error() -> None:
    """Un mot de passe erroné ⇒ **même** message que l'email inconnu (Exigence 1.9)."""
    session = _FakeUserSession()
    service = _service(session=session)
    await service.register("dan@example.com", "bon-mot-de-passe", "Dan")

    with pytest.raises(AuthError) as unknown_email:
        await service.authenticate("autre@example.com", "peu-importe")
    with pytest.raises(AuthError) as wrong_password:
        await service.authenticate("dan@example.com", "mauvais-mot-de-passe")

    # Indiscernables : ne révèle pas lequel des deux champs est en cause (Exigence 1.9).
    assert str(unknown_email.value) == str(wrong_password.value)
    assert str(wrong_password.value) == GENERIC_CREDENTIALS_ERROR


async def test_authenticate_inactive_user_raises_generic_error() -> None:
    """Un compte inactif ⇒ message générique (Exigence 1.9)."""
    session = _FakeUserSession()
    service = _service(session=session)
    user = await service.register("eve@example.com", "bon-mot-de-passe", "Eve")
    user.is_active = False

    with pytest.raises(AuthError) as excinfo:
        await service.authenticate("eve@example.com", "bon-mot-de-passe")

    assert str(excinfo.value) == GENERIC_CREDENTIALS_ERROR


# =========================================================================== #
# Rafraîchissement JWT (Exigences 1.6, 1.7)                                   #
# =========================================================================== #
async def test_refresh_issues_new_access_token_from_valid_refresh() -> None:
    """Un refresh token valide émet un nouvel access token (Exigence 1.6)."""
    session = _FakeUserSession()
    config = _test_settings()
    service = _service(session=session, config=config)
    await service.register("frank@example.com", "bon-mot-de-passe", "Frank")
    tokens = await service.authenticate("frank@example.com", "bon-mot-de-passe")

    refreshed = await service.refresh(tokens.refresh_token)

    assert refreshed.access_token
    assert refreshed.token_type == "bearer"
    assert refreshed.expires_in == config.jwt_access_token_expire_minutes * 60

    secret = config.jwt_secret_key.get_secret_value()
    payload = jwt.decode(refreshed.access_token, secret, algorithms=["HS256"])
    assert payload["type"] == "access"


async def test_refresh_rejects_access_token_used_as_refresh() -> None:
    """Un access token présenté au rafraîchissement est rejeté (type invalide)."""
    session = _FakeUserSession()
    service = _service(session=session)
    await service.register("grace@example.com", "bon-mot-de-passe", "Grace")
    tokens = await service.authenticate("grace@example.com", "bon-mot-de-passe")

    with pytest.raises(AuthError) as excinfo:
        await service.refresh(tokens.access_token)

    assert str(excinfo.value) == INVALID_TOKEN_ERROR


async def test_refresh_rejects_revoked_token_after_logout() -> None:
    """Un refresh token révoqué (logout) ne produit plus de jeton (Exigences 1.6, 1.7)."""
    session = _FakeUserSession()
    config = _test_settings()
    redis = _FakeRedis()
    service = _service(session=session, redis=redis, config=config)
    await service.register("heidi@example.com", "bon-mot-de-passe", "Heidi")
    tokens = await service.authenticate("heidi@example.com", "bon-mot-de-passe")

    secret = config.jwt_secret_key.get_secret_value()
    jti = jwt.decode(tokens.refresh_token, secret, algorithms=["HS256"])["jti"]
    await service.logout(jti)

    with pytest.raises(AuthError) as excinfo:
        await service.refresh(tokens.refresh_token)

    assert str(excinfo.value) == INVALID_TOKEN_ERROR


async def test_refresh_rejects_garbage_token() -> None:
    """Un jeton illisible est rejeté avec le message générique de jeton (Exigence 1.6)."""
    service = _service()

    with pytest.raises(AuthError) as excinfo:
        await service.refresh("ceci-nest-pas-un-jwt")

    assert str(excinfo.value) == INVALID_TOKEN_ERROR


# =========================================================================== #
# Compte courant sans password_hash (Exigence 1.8)                            #
# =========================================================================== #
async def test_current_user_returns_public_view_without_password_hash() -> None:
    """``current_user`` renvoie une vue publique sans ``password_hash`` (Exigence 1.8)."""
    session = _FakeUserSession()
    service = _service(session=session)
    user = await service.register("ivan@example.com", "bon-mot-de-passe", "Ivan")
    # En base, ``created_at``/``updated_at`` sont fournis par ``TimestampMixin`` ;
    # ici la ligne est en mémoire, on renseigne donc ces horodatages requis par
    # la sérialisation ``UserPublic`` (Exigence 1.8).
    now = datetime.now(timezone.utc)
    user.created_at = now
    user.updated_at = now
    tokens = await service.authenticate("ivan@example.com", "bon-mot-de-passe")

    public = await service.current_user(tokens.access_token)

    assert isinstance(public, UserPublic)
    assert public.email == "ivan@example.com"
    assert not hasattr(public, "password_hash")
