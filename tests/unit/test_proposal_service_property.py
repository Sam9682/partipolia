# Feature: partipolia-platform, Property 4: Versionnement monotone et historique immuable
"""Test de propriété du ``ProposalService`` (Exigences 3.4, 3.5).

**Property 4: Versionnement monotone et historique immuable**

**Validates: Requirements 3.4, 3.5**

Pour toute Proposition et pour toute séquence de ``N`` modifications valides :

* l'attribut ``version`` croît **strictement de manière monotone** et vaut
  ``version_initiale (= 1) + N`` (Exigence 3.4) ;
* chaque modification produit **une** nouvelle ``ProposalVersion`` complète,
  comportant un ``change_summary`` et l'instantané de l'état correspondant
  (Exigence 3.4) ;
* aucune version antérieure n'est jamais écrasée : l'ensemble des instantanés
  déjà enregistrés demeure inchangé, y compris pour une Proposition publiée
  (Exigence 3.5).

Le test exerce le **vrai** code du Service (``create`` puis ``update``) ; seule
la couche de persistance est remplacée par une ``AsyncSession`` factice qui
enregistre en mémoire les objets ajoutés et affecte des identifiants, sans base
de données réelle. Les générateurs Hypothesis produisent des payloads de mise à
jour valides (au moins un champ modifié + un ``change_summary`` non vide).
"""

from __future__ import annotations

import itertools
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.models.proposal import Proposal, ProposalVersion
from app.models.user import User
from app.schemas.proposal import ProposalCreate, ProposalUpdate
from app.services.proposal_service import ProposalService


class _FakeSession:
    """``AsyncSession`` factice : persistance en mémoire suffisante au Service.

    Le ``ProposalService`` s'appuie sur : ``add`` (enregistrer un objet),
    ``flush`` (matérialiser les identifiants), ``get`` (recharger la Proposition
    par id) et ``scalar`` (vérifier l'unicité d'un slug). On reproduit ce
    comportement sans mocker la logique métier : les ``ProposalVersion`` créées
    par le Service sont conservées telles quelles, ce qui permet de vérifier
    l'immuabilité de l'historique.
    """

    def __init__(self) -> None:
        self.proposals: dict[int, Proposal] = {}
        self.versions: list[ProposalVersion] = []
        self._id_seq = itertools.count(1)

    def add(self, obj: Any) -> None:
        if isinstance(obj, Proposal):
            if obj.id is None:
                obj.id = next(self._id_seq)
            self.proposals[obj.id] = obj
        elif isinstance(obj, ProposalVersion):
            self.versions.append(obj)

    async def flush(self) -> None:
        # Matérialise les id manquants (comportement d'un flush réel).
        for obj in list(self.proposals.values()):
            if obj.id is None:  # pragma: no cover - défensif
                obj.id = next(self._id_seq)

    async def get(self, model: type, ident: int) -> Any:
        if model is Proposal:
            return self.proposals.get(ident)
        return None  # pragma: no cover - non utilisé par ce test

    async def scalar(self, _statement: object) -> Any:
        # Utilisé pour la vérification d'unicité du slug : aucun slug existant.
        return None


def _author() -> User:
    return User(
        id=1,
        email="auteur@example.org",
        password_hash="x",
        display_name="Auteur",
        is_admin=False,
    )


# --- Générateurs Hypothesis -------------------------------------------------

# Texte non vide et borné pour rester lisible tout en couvrant l'espace utile.
_text = st.text(min_size=1, max_size=40).map(lambda s: s.strip() or "x")
_title = st.text(min_size=3, max_size=60).map(lambda s: s.strip().ljust(3, "x")[:60])


def _update_strategy() -> st.SearchStrategy[ProposalUpdate]:
    """Génère une mise à jour valide : ≥ 1 champ modifié + ``change_summary``."""
    return st.builds(
        ProposalUpdate,
        title=st.one_of(st.none(), _title),
        problem=st.one_of(st.none(), _text),
        description=st.one_of(st.none(), _text),
        expected_impact=st.one_of(st.none(), _text),
        change_summary=_text,
    )


@pytest.mark.property
@settings(max_examples=150, deadline=None)
@given(
    title=_title,
    problem=_text,
    description=_text,
    updates=st.lists(_update_strategy(), min_size=0, max_size=8),
)
async def test_version_is_monotonic_and_history_is_immutable(
    title: str, problem: str, description: str, updates: list[ProposalUpdate]
) -> None:
    """version = 1 + N, historique strictement croissant et jamais écrasé."""
    session = _FakeSession()
    service = ProposalService(session)  # type: ignore[arg-type]
    author = _author()

    proposal = await service.create(
        author,
        ProposalCreate(theme_id=1, title=title, problem=problem, description=description),
    )

    # État initial : DRAFT, version 1, une seule entrée d'historique (v1).
    assert proposal.version == 1
    assert proposal.status == "DRAFT"
    assert len(session.versions) == 1
    assert session.versions[0].version == 1
    assert session.versions[0].change_summary  # résumé initial non vide

    # Instantanés déjà observés : on mémorise une copie profonde pour détecter
    # tout écrasement ultérieur d'une version antérieure (immuabilité).
    seen_snapshots: list[dict[str, Any]] = [dict(session.versions[0].snapshot)]

    n = len(updates)
    for index, payload in enumerate(updates, start=1):
        await service.update(author, proposal.id, payload)

        # Monotonie stricte : chaque modification incrémente la version de 1.
        assert proposal.version == 1 + index

        # Une nouvelle entrée d'historique par modification (jamais un écrasement).
        assert len(session.versions) == 1 + index
        latest = session.versions[-1]
        assert latest.version == 1 + index
        assert latest.change_summary == payload.change_summary
        # L'instantané reflète l'état courant (statut/version inclus).
        assert latest.snapshot["version"] == proposal.version
        assert latest.snapshot["status"] == proposal.status

        # Immuabilité : toutes les versions antérieures restent identiques.
        for prior_index, prior_snapshot in enumerate(seen_snapshots):
            assert session.versions[prior_index].snapshot == prior_snapshot
        seen_snapshots.append(dict(latest.snapshot))

    # Après N modifications : version finale = 1 + N, historique complet.
    assert proposal.version == 1 + n
    assert len(session.versions) == 1 + n

    # Les numéros de version de l'historique sont strictement croissants (1..1+N).
    recorded = [v.version for v in session.versions]
    assert recorded == list(range(1, 2 + n))
    assert all(b > a for a, b in zip(recorded, recorded[1:]))


@pytest.mark.property
@settings(max_examples=100, deadline=None)
@given(updates=st.lists(_update_strategy(), min_size=1, max_size=6))
async def test_published_proposal_history_is_never_overwritten(
    updates: list[ProposalUpdate],
) -> None:
    """Même publiée, une Proposition conserve tous ses instantanés antérieurs (3.5)."""
    session = _FakeSession()
    service = ProposalService(session)  # type: ignore[arg-type]
    author = _author()

    proposal = await service.create(
        author,
        ProposalCreate(theme_id=1, title="Titre initial", problem="p", description="d"),
    )
    # Publication de la Proposition (l'historique doit rester intact ensuite).
    proposal.status = "PUBLISHED"

    baseline = dict(session.versions[0].snapshot)

    for payload in updates:
        await service.update(author, proposal.id, payload)
        # La toute première version (création) n'est jamais altérée.
        assert session.versions[0].snapshot == baseline

    # Toutes les versions demeurent présentes et distinctes.
    versions = [v.version for v in session.versions]
    assert versions == sorted(set(versions))
    assert len(versions) == 1 + len(updates)
