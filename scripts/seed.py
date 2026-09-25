"""Script d'amorçage de la base (Exigences 2.1, 18.1).

Amorce, de façon **idempotente** :

* exactement les **25 Thèmes** du Référentiel_Thématique V1 (slugs figés par
  l'Exigence 2) — un Thème absent est créé, un Thème déjà présent est laissé
  intact ;
* un **Programme** initial (statut ``DRAFT``) si aucun n'existe.

Utilisation :

    uv run python -m scripts.seed
    # ou
    python -m scripts.seed

Le script est rejouable sans effet de bord : relancer ``seed`` sur une base déjà
amorcée n'insère pas de doublon et ne remet pas à zéro les données existantes.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import func, select

from app.core.database import SessionLocal
from app.models import Program, Theme

# ---------------------------------------------------------------------------
# Référentiel_Thématique V1 : exactement 25 Thèmes (Exigence 2).
# L'ordre suit la note « Thèmes V1 (slugs) » de requirements.md.
# ---------------------------------------------------------------------------
THEMES_V1: list[tuple[str, str]] = [
    ("economie", "Économie"),
    ("finances-publiques", "Finances publiques"),
    ("fiscalite", "Fiscalité"),
    ("travail", "Travail"),
    ("sante", "Santé"),
    ("education", "Éducation"),
    ("recherche", "Recherche"),
    ("ia-numerique", "IA & Numérique"),
    ("industrie", "Industrie"),
    ("energie", "Énergie"),
    ("ecologie", "Écologie"),
    ("agriculture", "Agriculture"),
    ("logement", "Logement"),
    ("securite", "Sécurité"),
    ("justice", "Justice"),
    ("immigration", "Immigration"),
    ("defense", "Défense"),
    ("europe", "Europe"),
    ("affaires-etrangeres", "Affaires étrangères"),
    ("institutions", "Institutions"),
    ("services-publics", "Services publics"),
    ("transports", "Transports"),
    ("outre-mer", "Outre-mer"),
    ("famille", "Famille"),
    ("culture-sport", "Culture & Sport"),
]

# Invariant de sécurité : l'Exigence 2 fige le référentiel à exactement 25 Thèmes.
assert len(THEMES_V1) == 25, (
    f"Le référentiel doit contenir exactement 25 Thèmes (trouvé : {len(THEMES_V1)})."
)
assert len({slug for slug, _ in THEMES_V1}) == 25, "Les slugs de Thèmes doivent être uniques."


async def seed_themes(session) -> tuple[int, int]:
    """Insère les Thèmes manquants ; laisse intacts ceux déjà présents.

    Retourne le couple ``(créés, ignorés)``.
    """
    result = await session.execute(select(Theme.slug))
    existing_slugs = set(result.scalars().all())

    created = 0
    for slug, name in THEMES_V1:
        if slug in existing_slugs:
            continue
        session.add(Theme(slug=slug, name=name))
        created += 1

    skipped = len(THEMES_V1) - created
    return created, skipped


async def seed_initial_program(session) -> bool:
    """Crée un Programme initial (statut ``DRAFT``) s'il n'en existe aucun.

    Retourne ``True`` si un Programme a été créé, ``False`` s'il en existait déjà.
    """
    count = await session.scalar(select(func.count()).select_from(Program))
    if count and count > 0:
        return False
    session.add(Program(status="DRAFT"))
    return True


async def seed() -> None:
    """Point d'entrée asynchrone : amorce Thèmes et Programme de façon idempotente."""
    async with SessionLocal() as session:
        created, skipped = await seed_themes(session)
        program_created = await seed_initial_program(session)
        await session.commit()

    print(f"Thèmes : {created} créé(s), {skipped} déjà présent(s) (total attendu : 25).")
    print(
        "Programme initial : créé (DRAFT)."
        if program_created
        else "Programme initial : déjà présent, aucun changement."
    )
    print("Amorçage terminé.")


def main() -> None:
    """Point d'entrée synchrone pour ``python -m scripts.seed``."""
    asyncio.run(seed())


if __name__ == "__main__":
    main()
