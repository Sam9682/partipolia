"""Tests unitaires du ``ProgramService`` (Exigences 18 et 19).

Couvre la logique métier du Service sans base de données réelle : une session
factice fournit les objets/lignes qu'une vraie requête produirait, afin
d'exercer le **vrai code** du Service (branches de contrôle, calcul du taux de
soutien, non-décisionnalité de la vue « Programme en construction »).

Portée :

* ``get_program`` — absence de Programme ⇒ :class:`ProgramNotFoundError`
  (Exigence 18.1) ;
* ``add_proposal`` — Proposition inexistante ⇒ :class:`ProposalNotFoundError`
  (Exigence 18.2) ; création d'une association porteuse de ``priority`` et
  ``included_at`` (Exigences 18.2, 18.4) ;
* ``remove_proposal`` — association absente ⇒
  :class:`ProgramProposalNotFoundError` (Exigence 18.3) ; suppression sinon ;
* ``build_draft_view`` — filtrage sur le taux de soutien et
  ``is_definitive=False`` (calcul non décisionnel, Exigence 19.2).
"""

from __future__ import annotations

import pytest

from app.models.program import Program, ProgramProposal
from app.services.program_service import (
    DRAFT_MIN_SUPPORT_RATE,
    ProgramNotFoundError,
    ProgramProposalNotFoundError,
    ProgramService,
    ProposalNotFoundError,
)


class _Row:
    """Ligne de résultat factice exposant des attributs nommés (comme un ``Row``)."""

    def __init__(self, **kwargs: object) -> None:
        self.__dict__.update(kwargs)


class _ScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _ExecuteResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return self._rows


class _FakeSession:
    """``AsyncSession`` factice pilotée par des files de réponses prédéfinies.

    Elle ne mocke pas la logique du Service : elle renvoie, dans l'ordre, les
    valeurs qu'une vraie session produirait pour ``scalar`` / ``get`` /
    ``execute``, ce qui permet d'exercer les branches réelles du Service.
    """

    def __init__(
        self,
        *,
        scalars: list[object] | None = None,
        gets: dict[int, object] | None = None,
        scalars_all: list[list[object]] | None = None,
        execute_rows: list[object] | None = None,
    ) -> None:
        self._scalars = list(scalars or [])
        self._gets = gets or {}
        self._scalars_all = list(scalars_all or [])
        self._execute_rows = execute_rows or []
        self.added: list[object] = []
        self.deleted: list[object] = []

    async def scalar(self, _statement: object) -> object:
        return self._scalars.pop(0) if self._scalars else None

    async def scalars(self, _statement: object) -> _ScalarResult:
        rows = self._scalars_all.pop(0) if self._scalars_all else []
        return _ScalarResult(rows)

    async def get(self, _model: object, ident: int) -> object:
        return self._gets.get(ident)

    async def execute(self, _statement: object) -> _ExecuteResult:
        return _ExecuteResult(self._execute_rows)

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)

    async def flush(self) -> None:
        return None

    async def refresh(self, _obj: object) -> None:
        return None


@pytest.mark.unit
async def test_get_program_raises_when_absent() -> None:
    """Sans Programme amorcé, ``get_program`` lève ``ProgramNotFoundError`` (Exigence 18.1)."""
    service = ProgramService(_FakeSession(scalars=[None]))  # type: ignore[arg-type]
    with pytest.raises(ProgramNotFoundError):
        await service.get_program()


@pytest.mark.unit
async def test_add_proposal_missing_proposal_raises() -> None:
    """Ajouter une Proposition inexistante lève ``ProposalNotFoundError`` (Exigence 18.2)."""
    program = Program(id=1, status="DRAFT")
    session = _FakeSession(scalars=[program], gets={})  # get(42) -> None
    service = ProgramService(session)  # type: ignore[arg-type]
    with pytest.raises(ProposalNotFoundError):
        await service.add_proposal(42, priority=0)


@pytest.mark.unit
async def test_add_proposal_creates_association_with_priority_and_included_at() -> None:
    """L'ajout crée une association porteuse de ``priority`` et ``included_at`` (Exigences 18.2, 18.4)."""
    program = Program(id=1, status="DRAFT")
    proposal = object()  # existence suffit ; get(7) renvoie un objet non nul
    # scalar #1 : get_program ; scalar #2 : association existante (aucune).
    session = _FakeSession(scalars=[program, None], gets={7: proposal})
    service = ProgramService(session)  # type: ignore[arg-type]

    association = await service.add_proposal(7, priority=3)

    assert isinstance(association, ProgramProposal)
    assert association.program_id == 1
    assert association.proposal_id == 7
    assert association.priority == 3
    # Inclusion définitive explicite et traçable : ``included_at`` horodaté (Exigence 18.2).
    assert association.included_at is not None
    assert association in session.added


@pytest.mark.unit
async def test_add_proposal_existing_updates_priority() -> None:
    """Ré-inclure une Proposition met à jour la ``priority`` sans dupliquer l'association."""
    program = Program(id=1, status="DRAFT")
    proposal = object()
    existing = ProgramProposal(program_id=1, proposal_id=7, priority=0)
    session = _FakeSession(scalars=[program, existing], gets={7: proposal})
    service = ProgramService(session)  # type: ignore[arg-type]

    association = await service.add_proposal(7, priority=9)

    assert association is existing
    assert association.priority == 9
    assert association.included_at is not None
    # Aucune nouvelle association ajoutée (mise à jour de l'existante).
    assert session.added == []


@pytest.mark.unit
async def test_remove_proposal_missing_association_raises() -> None:
    """Retirer une association absente lève ``ProgramProposalNotFoundError`` (Exigence 18.3)."""
    program = Program(id=1, status="DRAFT")
    session = _FakeSession(scalars=[program, None])
    service = ProgramService(session)  # type: ignore[arg-type]
    with pytest.raises(ProgramProposalNotFoundError):
        await service.remove_proposal(5)


@pytest.mark.unit
async def test_remove_proposal_deletes_association() -> None:
    """Retirer une Proposition supprime l'association correspondante (Exigence 18.3)."""
    program = Program(id=1, status="DRAFT")
    association = ProgramProposal(program_id=1, proposal_id=5, priority=0)
    session = _FakeSession(scalars=[program, association])
    service = ProgramService(session)  # type: ignore[arg-type]

    await service.remove_proposal(5)

    assert association in session.deleted


@pytest.mark.unit
async def test_build_draft_view_is_never_definitive() -> None:
    """La vue « Programme en construction » n'est jamais définitive (Exigence 19.2)."""
    program = Program(id=1, status="DRAFT")
    session = _FakeSession(scalars=[program], execute_rows=[])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_draft_view()

    assert view.is_definitive is False
    assert view.entries == []
    # Aucune écriture dans la table de décision (calcul non décisionnel, Exigence 19.2).
    assert session.added == []
    assert session.deleted == []


@pytest.mark.unit
async def test_build_draft_view_filters_on_support_rate() -> None:
    """Seules les Propositions fortement soutenues sont retenues (Exigence 19.1).

    Une Proposition dont le taux de soutien est sous le seuil est écartée ; une
    Proposition au-dessus du seuil est conservée avec ses décomptes exposés comme
    résultat de participation.
    """
    program = Program(id=1, status="DRAFT")
    # Faiblement soutenue (taux 0.5 < seuil) : exclue.
    weak = _Row(
        proposal_id=10,
        slug="faible",
        title="Faible",
        theme_id=1,
        support_count=1,
        oppose_count=1,
        source_count=2,
    )
    # Fortement soutenue (taux ~0.9 ≥ seuil) : incluse.
    strong = _Row(
        proposal_id=11,
        slug="forte",
        title="Forte",
        theme_id=2,
        support_count=9,
        oppose_count=1,
        source_count=3,
    )
    session = _FakeSession(scalars=[program], execute_rows=[weak, strong])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_draft_view()

    assert view.is_definitive is False
    kept_ids = {entry.proposal_id for entry in view.entries}
    assert kept_ids == {11}
    (entry,) = view.entries
    assert entry.support_rate >= DRAFT_MIN_SUPPORT_RATE
    assert entry.support_count == 9
    assert entry.oppose_count == 1
    assert entry.source_count == 3
    assert entry.is_estimated is True


# --------------------------------------------------------------------------- #
# Statistiques du Programme (Exigence 18.5)                                    #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
async def test_statistics_aggregates_counts() -> None:
    """``statistics`` agrège inclusions, inclusions définitives et Thèmes (Exigence 18.5).

    Le Service émet quatre requêtes ``scalar`` dans l'ordre : ``get_program``,
    puis ``included_count``, ``definitive_count`` et ``theme_count``. La session
    factice les renvoie dans cet ordre pour exercer le calcul réel du schéma
    :class:`ProgramStatistics`.
    """
    program = Program(id=1, status="DRAFT")
    session = _FakeSession(scalars=[program, 5, 3, 2])
    service = ProgramService(session)  # type: ignore[arg-type]

    stats = await service.statistics()

    assert stats.program_id == 1
    assert stats.included_count == 5
    assert stats.definitive_count == 3
    assert stats.theme_count == 2
    # Calcul purement agrégé : aucune écriture (Exigence 18.5, anonymisation).
    assert session.added == []
    assert session.deleted == []


@pytest.mark.unit
async def test_statistics_coerces_null_counts_to_zero() -> None:
    """Des décomptes SQL nuls sont normalisés en 0 (agrégat robuste, Exigence 18.5)."""
    program = Program(id=1, status="DRAFT")
    # Un Programme vide : les agrégats ``COUNT`` peuvent remonter ``None``.
    session = _FakeSession(scalars=[program, None, None, None])
    service = ProgramService(session)  # type: ignore[arg-type]

    stats = await service.statistics()

    assert stats.included_count == 0
    assert stats.definitive_count == 0
    assert stats.theme_count == 0


@pytest.mark.unit
async def test_statistics_raises_when_no_program() -> None:
    """Sans Programme amorcé, ``statistics`` propage ``ProgramNotFoundError`` (Exigence 18.1)."""
    session = _FakeSession(scalars=[None])
    service = ProgramService(session)  # type: ignore[arg-type]
    with pytest.raises(ProgramNotFoundError):
        await service.statistics()


# --------------------------------------------------------------------------- #
# Vue « Programme en construction » — calcul non décisionnel (Exigence 19)     #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
async def test_build_draft_view_computes_support_rate_and_exposes_counts() -> None:
    """Chaque entrée conservée expose son taux de soutien et ses décomptes (Exigence 19.1).

    Le taux de soutien vaut ``support / (support + oppose)`` ; les décomptes
    absolus retenus sont exposés comme résultat de participation (Exigence 19.2).
    """
    program = Program(id=1, status="DRAFT")
    row = _Row(
        proposal_id=20,
        slug="mesure",
        title="Mesure",
        theme_id=4,
        support_count=8,
        oppose_count=2,
        source_count=5,
    )
    session = _FakeSession(scalars=[program], execute_rows=[row])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_draft_view()

    (entry,) = view.entries
    assert entry.proposal_id == 20
    assert entry.slug == "mesure"
    assert entry.title == "Mesure"
    assert entry.theme_id == 4
    assert entry.support_count == 8
    assert entry.oppose_count == 2
    # 8 / (8 + 2) = 0.8.
    assert entry.support_rate == pytest.approx(0.8)
    assert entry.source_count == 5
    assert entry.is_estimated is True


@pytest.mark.unit
async def test_build_draft_view_keeps_entry_at_support_rate_threshold() -> None:
    """Une Proposition juste au seuil de soutien est conservée (borne incluse, Exigence 19.1)."""
    program = Program(id=1, status="DRAFT")
    # 3 / (3 + 2) = 0.6 == DRAFT_MIN_SUPPORT_RATE : conservée (comparaison ``<``).
    at_threshold = _Row(
        proposal_id=30,
        slug="seuil",
        title="Au seuil",
        theme_id=1,
        support_count=3,
        oppose_count=2,
        source_count=1,
    )
    session = _FakeSession(scalars=[program], execute_rows=[at_threshold])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_draft_view()

    (entry,) = view.entries
    assert entry.proposal_id == 30
    assert entry.support_rate == pytest.approx(DRAFT_MIN_SUPPORT_RATE)


@pytest.mark.unit
async def test_build_draft_view_preserves_multiple_entries() -> None:
    """Plusieurs Propositions éligibles sont toutes restituées (Exigence 19.1)."""
    program = Program(id=1, status="DRAFT")
    first = _Row(
        proposal_id=40,
        slug="a",
        title="A",
        theme_id=1,
        support_count=7,
        oppose_count=1,
        source_count=2,
    )
    second = _Row(
        proposal_id=41,
        slug="b",
        title="B",
        theme_id=2,
        support_count=10,
        oppose_count=0,
        source_count=4,
    )
    session = _FakeSession(scalars=[program], execute_rows=[first, second])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_draft_view()

    assert view.is_definitive is False
    assert [entry.proposal_id for entry in view.entries] == [40, 41]
    # Un dénominateur (support + oppose) strictement positif ⇒ taux borné à 1.0.
    assert view.entries[1].support_rate == pytest.approx(1.0)


# --------------------------------------------------------------------------- #
# Vue publique de la page Programme (Exigences 24.2, 24.3)                     #
# --------------------------------------------------------------------------- #


@pytest.mark.unit
async def test_build_public_program_view_groups_measures_by_theme() -> None:
    """Les mesures incluses sont regroupées par Thème avec décomptes et estimations (Exigence 24.2).

    Deux mesures du même Thème et une mesure d'un autre Thème produisent deux
    groupes ; chaque groupe cumule le nombre de mesures, les Votes exprimés, le
    soutien agrégé et la somme des montants estimés renseignés.
    """
    program = Program(id=1, status="DRAFT")
    rows = [
        _Row(
            theme_id=10,
            theme_slug="mobilite",
            theme_name="Mobilité",
            proposal_id=1,
            proposal_slug="transport",
            proposal_title="Transport",
            estimated_cost=1000,
            estimated_revenue=None,
            estimated_savings=200,
            priority=0,
            support_count=8,
            oppose_count=2,
            participation_count=11,
        ),
        _Row(
            theme_id=10,
            theme_slug="mobilite",
            theme_name="Mobilité",
            proposal_id=2,
            proposal_slug="velo",
            proposal_title="Vélo",
            estimated_cost=None,
            estimated_revenue=None,
            estimated_savings=None,
            priority=1,
            support_count=3,
            oppose_count=3,
            participation_count=6,
        ),
        _Row(
            theme_id=20,
            theme_slug="ecologie",
            theme_name="Écologie",
            proposal_id=3,
            proposal_slug="arbres",
            proposal_title="Arbres",
            estimated_cost=500,
            estimated_revenue=50,
            estimated_savings=None,
            priority=0,
            support_count=5,
            oppose_count=0,
            participation_count=5,
        ),
    ]
    session = _FakeSession(scalars=[program], execute_rows=rows)
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_public_program_view()

    assert view.program_id == 1
    assert [t.theme_id for t in view.themes] == [10, 20]

    mobilite = view.themes[0]
    assert mobilite.measure_count == 2
    assert mobilite.proposal_count == 2
    assert mobilite.vote_count == 17  # 11 + 6
    assert mobilite.support_count == 11  # 8 + 3
    assert mobilite.oppose_count == 5  # 2 + 3
    # Taux agrégé 11 / (11 + 5).
    assert mobilite.support_rate == pytest.approx(11 / 16)
    # Somme des montants renseignés (les ``None`` n'entrent pas).
    assert mobilite.estimated_cost == pytest.approx(1000)
    assert mobilite.estimated_savings == pytest.approx(200)
    assert mobilite.estimated_revenue is None
    assert [m.proposal_id for m in mobilite.measures] == [1, 2]

    ecologie = view.themes[1]
    assert ecologie.measure_count == 1
    assert ecologie.estimated_revenue == pytest.approx(50)

    # Totaux du Programme.
    assert view.total_measure_count == 3
    assert view.total_vote_count == 22
    assert view.total_estimated_cost == pytest.approx(1500)
    assert view.total_estimated_revenue == pytest.approx(50)
    assert view.total_estimated_savings == pytest.approx(200)

    # Restitution factuelle : aucune écriture (page de consultation, Exigence 24.4).
    assert session.added == []
    assert session.deleted == []


@pytest.mark.unit
async def test_build_public_program_view_empty_program() -> None:
    """Un Programme sans mesure produit une vue vide sans montants inventés (Exigences 24.2, 24.3)."""
    program = Program(id=1, status="DRAFT")
    session = _FakeSession(scalars=[program], execute_rows=[])
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_public_program_view()

    assert view.themes == []
    assert view.total_measure_count == 0
    assert view.total_vote_count == 0
    # Aucun agrégat financier inventé lorsqu'aucun montant n'est renseigné.
    assert view.total_estimated_cost is None
    assert view.total_estimated_revenue is None
    assert view.total_estimated_savings is None


@pytest.mark.unit
async def test_build_public_program_view_support_rate_zero_without_votes() -> None:
    """Une mesure sans Vote a un taux de soutien nul (dénominateur nul, Exigence 7.2)."""
    program = Program(id=1, status="DRAFT")
    rows = [
        _Row(
            theme_id=10,
            theme_slug="mobilite",
            theme_name="Mobilité",
            proposal_id=1,
            proposal_slug="transport",
            proposal_title="Transport",
            estimated_cost=None,
            estimated_revenue=None,
            estimated_savings=None,
            priority=0,
            support_count=0,
            oppose_count=0,
            participation_count=0,
        ),
    ]
    session = _FakeSession(scalars=[program], execute_rows=rows)
    service = ProgramService(session)  # type: ignore[arg-type]

    view = await service.build_public_program_view()

    (theme,) = view.themes
    (measure,) = theme.measures
    assert measure.support_rate == 0.0
    assert theme.support_rate == 0.0
