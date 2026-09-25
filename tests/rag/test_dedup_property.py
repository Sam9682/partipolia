# Feature: partipolia-platform, Property 5: Idempotence de l'ingestion par checksum
"""Test de propriété du ``Deduplicator`` (Exigence 11.3).

**Property 5: Idempotence de l'ingestion par checksum**

**Validates: Requirements 11.3**

Pour tout contenu de Document :

* le checksum est **déterministe** — deux calculs sur le même contenu produisent
  la même valeur (base de l'idempotence) ;
* des contenus différents produisent des checksums différents (le SHA-256
  distingue le contenu) ;
* un Document dont le checksum est **déjà connu** est signalé comme tel
  (``is_known`` → ``True``), donc n'est pas réindexé (``documents.checksum``
  UNIQUE) ; à l'inverse un checksum inconnu renvoie ``False`` ;
* ingérer deux fois le même contenu revient à l'ingérer une seule fois : après
  la première ingestion (enregistrement du checksum), la seconde est reconnue
  comme déjà connue — l'ensemble des checksums enregistrés est identique que le
  contenu soit soumis une ou deux fois (idempotence).

Le test exerce le vrai code du module (calcul SHA-256 pur) et remplace la seule
dépendance externe — la vérification d'existence en base — par un ensemble
en mémoire jouant le rôle du ``SELECT 1 FROM documents WHERE checksum = :c``.
Aucun accès réseau ni base n'est requis ; le test est donc valable hors-ligne.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from app.rag.ingestion.dedup import Deduplicator

# Contenus textuels arbitraires (Unicode inclus) : l'encodage UTF-8 du module
# doit rester stable quel que soit le contenu.
_content = st.text(max_size=2000)


@settings(max_examples=200)
@given(content=_content)
def test_checksum_is_deterministic(content: str) -> None:
    """Deux calculs sur le même contenu produisent le même checksum."""
    dedup = Deduplicator()

    first = dedup.checksum(content)
    second = dedup.checksum(content)

    assert first == second
    # SHA-256 hexadécimal : 64 caractères, compatible ``documents.checksum``.
    assert len(first) == 64
    assert all(c in "0123456789abcdef" for c in first)


@settings(max_examples=200)
@given(pair=st.lists(_content, min_size=2, max_size=2, unique=True))
def test_distinct_content_yields_distinct_checksum(pair: list[str]) -> None:
    """Des contenus différents produisent des checksums différents."""
    a, b = pair
    dedup = Deduplicator()

    assert dedup.checksum(a) != dedup.checksum(b)


@settings(max_examples=200)
@given(content=_content, other=_content)
def test_is_known_reflects_registered_checksums(content: str, other: str) -> None:
    """``is_known`` renvoie True pour un checksum enregistré, False sinon."""
    registered: set[str] = set()
    dedup = Deduplicator(lookup=registered.__contains__)

    checksum = dedup.checksum(content)
    # Inconnu au départ.
    assert dedup.is_known(checksum) is False

    # Une fois enregistré, il est reconnu.
    registered.add(checksum)
    assert dedup.is_known(checksum) is True

    # Un checksum d'un autre contenu n'est connu que s'il coïncide (même contenu).
    other_checksum = dedup.checksum(other)
    assert dedup.is_known(other_checksum) is (other_checksum in registered)


@settings(max_examples=200)
@given(content=_content)
def test_ingesting_twice_equals_ingesting_once(content: str) -> None:
    """Ingérer le même contenu deux fois équivaut à l'ingérer une fois (idempotent).

    Simule l'orchestration : on n'enregistre le checksum que s'il est inconnu,
    puis on vérifie que soumettre le contenu une seconde fois ne change pas
    l'ensemble des checksums enregistrés (aucune réindexation).
    """
    registered: set[str] = set()
    dedup = Deduplicator(lookup=registered.__contains__)

    def ingest(text: str) -> bool:
        """Renvoie True si le contenu a été (ré)indexé, False s'il était déjà connu."""
        checksum = dedup.checksum(text)
        if dedup.is_known(checksum):
            return False
        registered.add(checksum)
        return True

    # Première ingestion : le contenu est nouveau, il est indexé.
    assert ingest(content) is True
    after_first = set(registered)

    # Seconde ingestion du même contenu : déjà connu, pas de réindexation.
    assert ingest(content) is False
    after_second = set(registered)

    # L'état est identique : une ou deux soumissions donnent le même résultat.
    assert after_second == after_first
    assert len(registered) == 1
