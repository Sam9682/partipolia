"""Schémas Pydantic v2 pour le Programme (Exigences 18 et 19).

Ces schémas définissent les contrats d'entrée/sortie du
:class:`~app.services.program_service.ProgramService` et de l'API du Programme
(``app/api/v1/program.py``) :

* :class:`ProgramPublic` — représentation publique du Programme
  (``GET /api/v1/program``, Exigence 18.1) ;
* :class:`ProgramProposalPublic` — association ``program_proposals`` porteuse de
  ``priority`` et ``included_at`` (Exigences 18.2, 18.4) renvoyée par
  ``GET /api/v1/program/proposals`` et ``POST /api/v1/program/proposals`` ;
* :class:`ProgramProposalAdd` — corps de ``POST /api/v1/program/proposals``
  (``proposal_id`` + ``priority``) (Exigence 18.2) ;
* :class:`ProgramStatistics` — statistiques agrégées du Programme
  (``GET /api/v1/program/statistics``, Exigence 18.5) ;
* :class:`DraftProgramEntry` / :class:`DraftProgramView` — vue « Programme en
  construction » : **calcul de restitution non décisionnel** (Exigence 19). Le
  champ :attr:`DraftProgramView.is_definitive` vaut toujours ``False`` afin de
  matérialiser que cette vue n'est jamais traitée comme un Programme définitif
  (Exigence 19.2).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProgramPublic(BaseModel):
    """Représentation publique du Programme (Exigence 18.1)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    created_at: datetime


class ProgramProposalPublic(BaseModel):
    """Association ``program_proposals`` (Exigences 18.2, 18.4).

    ``priority`` et ``included_at`` sont des attributs de l'association, jamais de
    la Proposition (Exigence 18.4). ``included_at`` matérialise l'inclusion
    définitive, explicite et traçable (Exigence 18.2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    program_id: int
    proposal_id: int
    priority: int
    included_at: datetime | None = None


class ProgramProposalAdd(BaseModel):
    """Corps de ``POST /api/v1/program/proposals`` (Exigence 18.2).

    Ajoute une Proposition au Programme avec une ``priority`` (attribut de
    l'association, Exigence 18.4). L'inclusion est un acte d'administration
    explicite et traçable (horodaté via ``included_at``).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    proposal_id: int = Field(gt=0)
    priority: int = Field(default=0, ge=0)


class ProgramStatistics(BaseModel):
    """Statistiques agrégées du Programme (Exigence 18.5).

    Les décomptes sont agrégés et anonymisés (aucune donnée individuelle) :

    * ``program_id`` — identifiant du Programme concerné ;
    * ``included_count`` — nombre de Propositions incluses (associations) ;
    * ``definitive_count`` — nombre d'inclusions définitives (``included_at`` non nul) ;
    * ``theme_count`` — nombre de Thèmes distincts couverts par les Propositions
      incluses.
    """

    program_id: int
    included_count: int = Field(ge=0)
    definitive_count: int = Field(ge=0)
    theme_count: int = Field(ge=0)


class DraftProgramEntry(BaseModel):
    """Proposition candidate de la vue « Programme en construction » (Exigence 19.1).

    Chaque entrée expose les décomptes ayant justifié sa sélection (fortement
    soutenue, documentée, estimée) ; ces valeurs sont un **résultat de
    participation** (Exigence 19.2), jamais une décision d'inclusion.
    """

    model_config = ConfigDict(from_attributes=True)

    proposal_id: int
    slug: str
    title: str
    theme_id: int
    support_count: int = Field(ge=0)
    oppose_count: int = Field(ge=0)
    support_rate: float = Field(ge=0.0, le=1.0)
    source_count: int = Field(ge=0)
    is_estimated: bool


class DraftProgramView(BaseModel):
    """Vue « Programme en construction » (Exigence 19).

    Regroupe les Propositions fortement soutenues, suffisamment documentées, non
    contradictoires et estimées financièrement (Exigence 19.1). C'est un **calcul
    de restitution non décisionnel** : :attr:`is_definitive` vaut toujours
    ``False`` (Exigence 19.2). L'inclusion définitive reste explicite et traçable
    via ``add_proposal`` (Exigences 19.3, 18.2).
    """

    entries: list[DraftProgramEntry] = Field(default_factory=list)
    is_definitive: bool = False


class ProgramMeasure(BaseModel):
    """Mesure (Proposition) restituée sur la page Programme (Exigences 24.2, 24.3).

    Chaque mesure expose ses décomptes de participation et ses agrégats
    financiers estimés. Les montants sont **facultatifs** (``None`` lorsque la
    mesure n'a pas été estimée), afin de ne jamais présenter d'agrégat inventé
    (Exigence 24.3). Le soutien est restitué à la fois en valeurs absolues
    (``support_count``/``oppose_count``) et en taux (``support_rate``) pour
    qu'aucun pourcentage ne soit affiché seul (Exigence 7.4).
    """

    model_config = ConfigDict(from_attributes=True)

    proposal_id: int
    slug: str
    title: str
    support_count: int = Field(ge=0)
    oppose_count: int = Field(ge=0)
    participation_count: int = Field(ge=0)
    support_rate: float = Field(ge=0.0, le=1.0)
    estimated_cost: float | None = None
    estimated_revenue: float | None = None
    estimated_savings: float | None = None


class ProgramThemeGroup(BaseModel):
    """Regroupement des mesures par Thème sur la page Programme (Exigence 24.2).

    Porte, pour chaque Thème, le nombre de mesures, le total de Votes exprimés et
    les agrégats financiers estimés (somme des montants renseignés des mesures).
    Les agrégats financiers sont des **estimations** dont les hypothèses sont
    explicitées sur la page (Exigence 24.3).
    """

    theme_id: int
    slug: str
    name: str
    measure_count: int = Field(ge=0)
    proposal_count: int = Field(ge=0)
    vote_count: int = Field(ge=0)
    support_count: int = Field(ge=0)
    oppose_count: int = Field(ge=0)
    support_rate: float = Field(ge=0.0, le=1.0)
    estimated_cost: float | None = None
    estimated_revenue: float | None = None
    estimated_savings: float | None = None
    measures: list[ProgramMeasure] = Field(default_factory=list)


class ProgramPageView(BaseModel):
    """Vue complète de la page Programme ``/programme`` (Exigences 24.2, 24.3).

    Restitue les mesures incluses au Programme regroupées par Thème, avec les
    totaux financiers estimés du Programme. C'est une **restitution factuelle** de
    la participation et des estimations : elle n'induit aucune décision et affiche
    explicitement les hypothèses des agrégats financiers (Exigence 24.3).
    """

    program_id: int
    themes: list[ProgramThemeGroup] = Field(default_factory=list)
    total_measure_count: int = Field(ge=0)
    total_vote_count: int = Field(ge=0)
    total_estimated_cost: float | None = None
    total_estimated_revenue: float | None = None
    total_estimated_savings: float | None = None
