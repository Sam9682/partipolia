"""Script CLI de création d'un compte Administrateur (Exigences 1.1, 1.3, 18.1).

Crée un Utilisateur avec ``is_admin=true`` et un mot de passe **haché** (jamais
stocké en clair — Exigence 1.3). L'email est UNIQUE (Exigence 1.1) : si un compte
existe déjà pour cet email, le script s'arrête sans le modifier.

Le mot de passe est haché via ``app.services.auth_service.hash_password`` lorsque
ce service est disponible (tâche 3.1) ; à défaut, le script utilise directement
``argon2-cffi`` afin de rester découplé du service d'authentification en cours de
développement.

Utilisation (arguments ou variables d'environnement) :

    uv run python -m scripts.create_admin --email admin@exemple.fr \\
        --password "motdepasse" --display-name "Admin"

    # ou via l'environnement :
    ADMIN_EMAIL=admin@exemple.fr ADMIN_PASSWORD=motdepasse \\
        ADMIN_DISPLAY_NAME="Admin" uv run python -m scripts.create_admin

Les arguments de ligne de commande ont priorité sur les variables d'environnement.
"""

from __future__ import annotations

import argparse
import asyncio
import os

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import User


def hash_password(password: str) -> str:
    """Hache un mot de passe (argon2), sans jamais conserver le clair (Exigence 1.3).

    Réutilise ``auth_service.hash_password`` s'il est disponible (tâche 3.1) afin de
    garantir une stratégie de hachage cohérente avec l'authentification. En repli
    (service pas encore livré), utilise directement ``argon2-cffi``.
    """
    try:
        from app.services.auth_service import hash_password as _service_hash

        return _service_hash(password)
    except Exception:
        # Repli défensif : hachage argon2 direct (paramètres par défaut robustes).
        from argon2 import PasswordHasher

        return PasswordHasher().hash(password)


async def create_admin(email: str, password: str, display_name: str) -> bool:
    """Crée un Administrateur si l'email est libre.

    Retourne ``True`` si le compte a été créé, ``False`` s'il existait déjà.
    """
    async with SessionLocal() as session:
        existing = await session.scalar(select(User).where(User.email == email))
        if existing is not None:
            return False

        session.add(
            User(
                email=email,
                password_hash=hash_password(password),
                display_name=display_name,
                is_admin=True,
                is_active=True,
                is_verified=True,
            )
        )
        await session.commit()
    return True


def _parse_args() -> argparse.Namespace:
    """Analyse les arguments CLI, avec repli sur les variables d'environnement."""
    parser = argparse.ArgumentParser(
        prog="create_admin",
        description="Crée un compte Administrateur (is_admin=true) avec mot de passe haché.",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("ADMIN_EMAIL"),
        help="Email de l'Administrateur (ou variable ADMIN_EMAIL).",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("ADMIN_PASSWORD"),
        help="Mot de passe en clair (ou variable ADMIN_PASSWORD) — haché avant stockage.",
    )
    parser.add_argument(
        "--display-name",
        dest="display_name",
        default=os.getenv("ADMIN_DISPLAY_NAME", "Administrateur"),
        help="Nom affiché (ou variable ADMIN_DISPLAY_NAME, défaut : « Administrateur »).",
    )
    return parser.parse_args()


def main() -> None:
    """Point d'entrée synchrone pour ``python -m scripts.create_admin``."""
    args = _parse_args()

    if not args.email or not args.password:
        raise SystemExit(
            "Email et mot de passe requis : fournissez --email / --password "
            "ou les variables ADMIN_EMAIL / ADMIN_PASSWORD."
        )

    created = asyncio.run(create_admin(args.email, args.password, args.display_name))
    if created:
        print(f"Administrateur créé : {args.email} (is_admin=true).")
    else:
        print(f"Un compte existe déjà pour {args.email} — aucune modification.")


if __name__ == "__main__":
    main()
