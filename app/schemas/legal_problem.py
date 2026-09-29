"""Schémas Pydantic v2 « Réparer la loi » (Exigences 1–14).

Ce module regroupe :

* les schémas de **Problème Juridique** et de **Réforme_Proposée**
  (:class:`LegalProblemCreate`, :class:`LegalProblemSummary`,
  :class:`LegalProblemDetail`, :class:`ReformView` — tâche 3.1) ;
* les schémas **d'actions citoyennes** (vote, prise de position, amendement,
  signalement d'effet secondaire, demande d'explication — tâche 3.3) ;
* les schémas **de sortie des analyses IA** matérialisées
  (:class:`AnalysisResult`, :class:`Unavailable`, :class:`SimulationResult`,
  :class:`ReformAnalysisBundle`, :class:`TechnicalConclusion` — tâche 3.3).

Principe directeur (Exigences 11, 12) : l'IA **n'écrit ni ne recommande** de
décision. Chaque sortie d'analyse expose ses **Sources** citées, ses **marqueurs**
de catégorie (fait / estimation / opinion / hypothèse / désaccord) et un indice de
confiance ; la :class:`TechnicalConclusion` reste **non prescriptive** (aucune
recommandation d'adoption/rejet, aucun classement des réformes — Property 18).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------------------
# Constantes de domaine partagées
# ---------------------------------------------------------------------------

# Longueur maximale des contributions libres (amendement, signalement) — Exigence 8.9.
MAX_CONTRIBUTION_LENGTH = 5000

# Valeur d'un Vote_Citoyen : soutien (+1), neutre (0), opposition (-1) — Exigence 8.1.
VoteValueLiteral = Literal[-1, 0, 1]

# Prise de position possible sur une Réforme_Proposée — Exigences 13.3, 13.4.
PositionLiteral = Literal["FOR", "AGAINST"]

# Catégories de marqueur d'un énoncé d'analyse — Exigences 11.6, 16.5.
MarkerCategory = Literal["fait", "estimation", "opinion", "hypothese", "desaccord"]

# Horizons temporels de la Simulation_De_Conséquences — Exigences 5.2, 5.3.
HorizonLiteral = Literal["1_an", "5_ans", "10_ans"]

# Statut d'une analyse IA matérialisée — modèle `legal_analyses`.
AnalysisStatusLiteral = Literal["PENDING", "READY", "INDISPONIBLE"]

# Niveau de complexité d'un Problème_Juridique — Exigences 2.2, 2.3.
# Borné à cet ensemble : toute valeur hors-ensemble est rejetée en validation (→ 422).
ComplexityLevel = Literal["FAIBLE", "MOYEN", "ELEVE"]


# ---------------------------------------------------------------------------
# Schémas Problème_Juridique et Réforme_Proposée (tâche 3.1)
# ---------------------------------------------------------------------------
#
# Les points d'accès de liste renvoient une enveloppe de pagination
# ``Page[LegalProblemSummary]`` (voir :class:`app.schemas.common.Page` :
# ``limit`` par défaut 20, borné à 100, avec ``total``).


class LegalProblemCreate(BaseModel):
    """Corps de création (admin) d'un Problème_Juridique (Exigences 2.1–2.4).

    * ``theme_id`` rattache le problème à exactement un Thème (> 0 — Exigence 2.4) ;
    * ``complexity_level`` est borné à :data:`ComplexityLevel` — une valeur
      hors ``{FAIBLE, MOYEN, ELEVE}`` est refusée en validation (→ 422 ;
      Exigences 2.2, 2.3) ;
    * ``affected_citizens_count`` ≥ 0 (défaut 0) ;
    * ``concerned_legal_texts`` / ``jurisprudence_refs`` valent des listes vides
      par défaut (Exigence 2.1).

    ``str_strip_whitespace=True`` normalise les chaînes en entrée.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    theme_id: int = Field(gt=0, description="Thème de rattachement (> 0 — Exigence 2.4).")
    title: str = Field(
        min_length=1,
        max_length=255,
        description="Titre du Problème_Juridique.",
    )
    summary: str = Field(min_length=1, description="Résumé décrivant le dysfonctionnement légal.")
    complexity_level: ComplexityLevel = Field(
        description="Niveau de complexité : FAIBLE, MOYEN ou ELEVE (Exigences 2.2, 2.3).",
    )
    affected_citizens_count: int = Field(
        default=0,
        ge=0,
        description="Nombre de citoyens concernés (≥ 0).",
    )
    concerned_legal_texts: list[str] = Field(
        default_factory=list,
        description="Textes de loi concernés (liste, vide par défaut).",
    )
    jurisprudence_refs: list[str] = Field(
        default_factory=list,
        description="Références de jurisprudence (liste, vide par défaut).",
    )


class ReformView(BaseModel):
    """Vue d'une Réforme_Proposée rattachée à un Problème_Juridique (Exigence 4.4).

    Projette le lien d'association (``legal_problem_reforms``) et la Proposition
    rattachée : identifiants, ``slug``/``title``/``status`` de la Réforme et le
    marqueur ``is_status_quo`` (au plus un statu quo par problème — Exigence 4.3).
    """

    model_config = ConfigDict(from_attributes=True)

    reform_id: int = Field(description="Identifiant de la Réforme_Proposée (Proposal).")
    problem_id: int = Field(description="Identifiant du Problème_Juridique rattaché.")
    proposal_id: int = Field(description="Identifiant de la Proposition sous-jacente.")
    slug: str = Field(description="Slug de la Réforme_Proposée.")
    title: str = Field(description="Titre de la Réforme_Proposée.")
    status: str = Field(description="Statut de la Réforme_Proposée.")
    is_status_quo: bool = Field(
        default=False,
        description="Marqueur statu quo du lien (au plus un par problème — Exigence 4.3).",
    )


class LegalProblemSummary(BaseModel):
    """Vue résumée d'un Problème_Juridique pour les listes (Exigences 1.3, 1.4).

    Renvoyée dans une enveloppe ``Page[LegalProblemSummary]`` par les points
    d'accès de liste. ``from_attributes=True`` autorise la construction directe
    depuis l'entité ORM :class:`app.models.legal_problem.LegalProblem`.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Identifiant unique du Problème_Juridique.")
    slug: str = Field(description="Slug unique du Problème_Juridique.")
    title: str = Field(description="Titre du Problème_Juridique.")
    summary: str = Field(description="Résumé décrivant le dysfonctionnement légal.")
    theme_id: int = Field(description="Thème de rattachement.")
    affected_citizens_count: int = Field(description="Nombre de citoyens concernés.")
    concerned_legal_texts: list[str] = Field(
        default_factory=list,
        description="Textes de loi concernés.",
    )
    jurisprudence_refs: list[str] = Field(
        default_factory=list,
        description="Références de jurisprudence.",
    )
    complexity_level: ComplexityLevel = Field(
        description="Niveau de complexité : FAIBLE, MOYEN ou ELEVE.",
    )
    status: str = Field(description="Statut de publication du Problème_Juridique.")


class LegalProblemDetail(LegalProblemSummary):
    """Vue détaillée d'un Problème_Juridique (Exigences 1.3, 4.4).

    Étend :class:`LegalProblemSummary` en incluant les Réformes_Proposées
    rattachées (:class:`ReformView`).
    """

    reforms: list[ReformView] = Field(
        default_factory=list,
        description="Réformes_Proposées rattachées au Problème_Juridique (Exigence 4.4).",
    )


# ---------------------------------------------------------------------------
# Schémas d'actions citoyennes (tâche 3.3)
# ---------------------------------------------------------------------------


class VoteInput(BaseModel):
    """Corps d'un Vote_Citoyen : ``{value ∈ {-1, 0, +1}}`` (Exigences 8.1, 14.5).

    ``value`` hors de ``{-1, 0, +1}`` est rejeté en validation (→ 422), l'état de
    la Plateforme restant inchangé (Exigence 14.5).
    """

    value: VoteValueLiteral = Field(
        description="Valeur du vote : +1 (soutien), 0 (neutre) ou -1 (opposition).",
    )


class VoteCounts(BaseModel):
    """Décomptes d'une Réforme_Proposée exposés par « Réparer la loi » (Exigence 9.1).

    ``participation_count`` vaut **explicitement** ``support_count + oppose_count``
    (les Votes NEUTRE sont exclus de ce décompte — Exigence 9.1, Property 2). Tout
    affichage d'un pourcentage de soutien s'accompagne du nombre absolu (Exigence
    9.3).
    """

    support_count: int = Field(default=0, ge=0, description="Nombre de Votes +1 (soutien).")
    oppose_count: int = Field(default=0, ge=0, description="Nombre de Votes -1 (opposition).")
    participation_count: int = Field(
        default=0,
        ge=0,
        description="Participation exprimée = support_count + oppose_count (NEUTRE exclu).",
    )


class PositionInput(BaseModel):
    """Corps d'une prise de position : ``{position ∈ {FOR, AGAINST}}`` (Exigences 13.3, 13.4).

    Une valeur hors de ``{FOR, AGAINST}`` est refusée en validation (→ 422 ;
    Exigence 13.4). Un contenu argumentaire facultatif accompagne la position.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    position: PositionLiteral = Field(
        description="Sens de la prise de position : FOR (pour) ou AGAINST (contre).",
    )
    content: str = Field(
        default="",
        max_length=MAX_CONTRIBUTION_LENGTH,
        description="Argument facultatif accompagnant la prise de position.",
    )


class AmendmentInput(BaseModel):
    """Corps d'un Amendement (Exigences 8.7, 8.9, 10).

    ``content`` est accepté si et seulement si sa longueur (après suppression des
    espaces de bordure) est comprise entre 1 et 5000 caractères inclus (Exigence
    8.9, Property 6). Un ``change_summary`` court documente la modification.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(
        min_length=1,
        max_length=MAX_CONTRIBUTION_LENGTH,
        description="Nouveau texte proposé pour la Réforme (1..5000 caractères).",
    )
    change_summary: str = Field(
        default="",
        max_length=500,
        description="Résumé facultatif de la modification apportée.",
    )


class SideEffectInput(BaseModel):
    """Corps d'un Signalement_D_Effet_Secondaire (Exigences 8.8, 8.9).

    ``content`` est accepté si et seulement si sa longueur (après suppression des
    espaces de bordure) est comprise entre 1 et 5000 caractères inclus (Exigence
    8.9, Property 6). Le signalement est ensuite soumis à modération.
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    content: str = Field(
        min_length=1,
        max_length=MAX_CONTRIBUTION_LENGTH,
        description="Effet secondaire signalé (1..5000 caractères).",
    )


class ExplainInput(BaseModel):
    """Corps d'une demande d'explication (« Demander une explication ») (Exigences 8.5, 8.6).

    ``question`` est la question adressée à l'assistant conversationnel RAG. La
    réponse cite au moins une Source ; en l'absence de Source, un message
    d'absence est renvoyé sans altérer un Vote_Citoyen existant (Exigence 8.6).
    """

    model_config = ConfigDict(str_strip_whitespace=True)

    question: str = Field(
        min_length=1,
        max_length=4000,
        description="Question adressée à l'assistant documentaire.",
    )


# ---------------------------------------------------------------------------
# Schémas de sortie des analyses IA (tâche 3.3)
# ---------------------------------------------------------------------------


class AnalysisSource(BaseModel):
    """Source citée dans une analyse IA (Exigences 3.7, 12.5).

    Reprend une citation numérotée et l'URL Légifrance/jurisprudence afin
    d'afficher l'ancrage documentaire de chaque affirmation.
    """

    number: int = Field(ge=1, description="Numéro de citation [n] affiché dans l'analyse.")
    chunk_id: int | None = Field(default=None, description="Fragment (document_chunks) cité.")
    source_id: int | None = Field(default=None, description="Identifiant de la Source d'origine.")
    document_id: int | None = Field(default=None, description="Identifiant du Document d'origine.")
    url: str | None = Field(
        default=None,
        description="Lien Légifrance/jurisprudence vers la Source citée (Exigence 12.5).",
    )


class AnalysisMarker(BaseModel):
    """Marqueur de catégorie d'un énoncé d'analyse (Exigences 11.6, 16.5).

    Distingue explicitement fait / estimation / opinion / hypothèse / désaccord,
    afin qu'aucune valeur projetée ne soit présentée comme un fait établi.
    """

    category: MarkerCategory = Field(
        description="Catégorie de l'énoncé : fait, estimation, opinion, hypothese ou desaccord.",
    )
    label: str = Field(
        description="Libellé visible du marqueur affiché à l'Utilisateur.",
    )


class AnalysisResult(BaseModel):
    """Résultat d'une analyse IA matérialisée disponible (Exigences 3, 7, 12.6).

    ``answer`` est la synthèse générée **uniquement** à partir du contexte
    documentaire récupéré ; ``sources`` liste les citations numérotées ;
    ``markers`` porte les marqueurs de catégorie ; ``confidence`` ∈ ``[0, 1]``
    reflète l'ancrage documentaire (abaissé pour les affirmations non sourcées —
    Property 12).
    """

    kind: str = Field(description="Agent IA à l'origine de l'analyse (voir AgentKind).")
    status: Literal["READY"] = Field(
        default="READY",
        description="Statut de l'analyse : disponible (matérialisée).",
    )
    answer: str = Field(description="Synthèse générée à partir du contexte documentaire.")
    sources: list[AnalysisSource] = Field(
        default_factory=list,
        description="Citations numérotées ancrant l'analyse (Exigences 3.7, 12.5).",
    )
    markers: list[AnalysisMarker] = Field(
        default_factory=list,
        description="Marqueurs de catégorie des énoncés (Exigences 11.6, 16.5).",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Indice de confiance dans l'ancrage documentaire de l'analyse.",
    )
    doc_version: str | None = Field(
        default=None,
        description="Empreinte du corpus documentaire ayant produit l'analyse.",
    )


class Unavailable(BaseModel):
    """Analyse indisponible : retrieval vide ou en échec (Exigences 3.5, 6.5, 7.10).

    Aucune génération n'a eu lieu et aucune référence n'a été fabriquée. Le champ
    ``status`` vaut ``INDISPONIBLE`` et ``reason`` porte un message d'indisponibilité
    **sans détail technique interne** (Exigence 16.7).
    """

    kind: str = Field(description="Agent IA concerné par l'indisponibilité.")
    status: Literal["INDISPONIBLE"] = Field(
        default="INDISPONIBLE",
        description="Statut : analyse momentanément indisponible.",
    )
    reason: str = Field(
        default="Analyse momentanément indisponible.",
        description="Message d'indisponibilité affichable, sans détail technique.",
    )


class HorizonEffect(BaseModel):
    """Effet projeté à un horizon donné pour une Catégorie_D_Acteur (Exigences 5.2, 5.3).

    Chaque effet porte un marqueur d'hypothèse/estimation visible (Exigence 5.5) :
    aucune projection n'est présentée comme un fait établi.
    """

    horizon: HorizonLiteral = Field(description="Horizon temporel : 1_an, 5_ans ou 10_ans.")
    effect: str = Field(description="Effet projeté à cet horizon.")
    marker: AnalysisMarker = Field(
        description="Marqueur d'hypothèse/estimation de la valeur projetée (Exigence 5.5).",
    )


class ActorSimulation(BaseModel):
    """Effets simulés pour une Catégorie_D_Acteur aux horizons 1/5/10 ans (Exigence 5.2).

    ``horizons`` fournit **exactement un** effet par horizon 1 an / 5 ans / 10 ans
    (complétude des horizons — Property 14).
    """

    actor_category: str = Field(description="Catégorie d'acteur concernée par la simulation.")
    horizons: list[HorizonEffect] = Field(
        description="Effets projetés, un par horizon 1/5/10 ans (Exigences 5.2, 5.3).",
    )


class SimulationResult(BaseModel):
    """Simulation_De_Conséquences « LoiLab » d'une Réforme_Proposée (Exigence 5).

    ``actors`` liste les Catégories_D_Acteur simulées, chacune couvrant les
    horizons 1/5/10 ans ; ``sources`` ancre la simulation dans le corpus documentaire.
    """

    kind: Literal["SIMULATION"] = Field(default="SIMULATION")
    status: Literal["READY"] = Field(default="READY")
    actors: list[ActorSimulation] = Field(
        default_factory=list,
        description="Effets simulés par Catégorie_D_Acteur (grille acteur × horizon).",
    )
    sources: list[AnalysisSource] = Field(
        default_factory=list,
        description="Citations numérotées ancrant la simulation.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Indice de confiance de la simulation.",
    )


class RiskItem(BaseModel):
    """Risque de la Détection_D_Effets_Pervers (Exigences 6.2, 6.3).

    Chaque risque porte un marqueur « risque identifié » (Exigence 6.3) et **au
    moins une** contre-mesure possible (Exigence 6.2, Property 16).
    """

    description: str = Field(description="Description du risque identifié.")
    marker: str = Field(
        default="risque identifié",
        description="Marqueur visible « risque identifié » (Exigence 6.3).",
    )
    countermeasures: list[str] = Field(
        min_length=1,
        description="Contre-mesures possibles (au moins une — Exigence 6.2).",
    )


class TechnicalConclusion(BaseModel):
    """Conclusion_Technique agrégée **non prescriptive** (Exigences 4.7, 7.9, 11).

    La synthèse est purement descriptive : elle **ne comporte aucune** recommandation
    d'adoption/rejet, aucune recommandation de vote ni classement des réformes
    (Property 18). Elle préserve les marqueurs de catégorie et les Sources citées.
    """

    summary: str = Field(description="Synthèse descriptive des analyses disponibles.")
    markers: list[AnalysisMarker] = Field(
        default_factory=list,
        description="Marqueurs de catégorie préservés (fait/estimation/opinion/…).",
    )
    sources: list[AnalysisSource] = Field(
        default_factory=list,
        description="Sources citées préservées dans la conclusion.",
    )
    unavailable_roles: list[str] = Field(
        default_factory=list,
        description="Rôles (ex. DEFENSEUR/OPPOSANT) sans argument documenté, signalés.",
    )


class ReformAnalysisBundle(BaseModel):
    """Ensemble des analyses d'une Réforme_Proposée (Exigences 7, 11).

    Rassemble les analyses d'agents disponibles ou indisponibles, la Simulation, la
    Détection_D_Effets_Pervers et la :class:`TechnicalConclusion` agrégée à partir
    des seuls agents disponibles (Exigence 7.10).
    """

    reform_id: int = Field(description="Identifiant de la Réforme_Proposée (Proposal).")
    analyses: list[AnalysisResult | Unavailable] = Field(
        default_factory=list,
        description="Analyses par agent IA (disponibles ou indisponibles).",
    )
    simulation: SimulationResult | Unavailable | None = Field(
        default=None,
        description="Simulation_De_Conséquences si disponible.",
    )
    side_effects: list[RiskItem] = Field(
        default_factory=list,
        description="Risques de la Détection_D_Effets_Pervers (chacun avec contre-mesure).",
    )
    conclusion: TechnicalConclusion | None = Field(
        default=None,
        description="Conclusion_Technique agrégée non prescriptive.",
    )


__all__ = [
    "MAX_CONTRIBUTION_LENGTH",
    "ComplexityLevel",
    "LegalProblemCreate",
    "ReformView",
    "LegalProblemSummary",
    "LegalProblemDetail",
    "VoteInput",
    "VoteCounts",
    "PositionInput",
    "AmendmentInput",
    "SideEffectInput",
    "ExplainInput",
    "AnalysisSource",
    "AnalysisMarker",
    "AnalysisResult",
    "Unavailable",
    "HorizonEffect",
    "ActorSimulation",
    "SimulationResult",
    "RiskItem",
    "TechnicalConclusion",
    "ReformAnalysisBundle",
]
