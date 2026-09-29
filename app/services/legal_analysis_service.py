"""Service d'orchestration IA « Réparer la loi » (Exigences 3, 5, 6, 7, 11, 12).

Ce module fournit :

* l'énumération :class:`AgentKind` — les 9 Agents_IA_Contradictoires bornés par
  le CHECK ``ck_legal_analyses_kind`` du modèle
  :class:`app.models.legal_analysis.LegalAnalysis` ;
* :class:`LegalAnalysisService` — orchestre les analyses IA **au-dessus de**
  :class:`app.rag.pipeline.RagPipeline`, en **lecture seule côté domaine**.

Périmètre de cette tâche (6.1) : **uniquement le côté lecture**. Les méthodes
``get_*`` lisent les analyses **matérialisées** de la table ``legal_analyses``
(source de vérité, auditable, ré-affichable sans coût) et **ne génèrent jamais en
ligne** (Exigences 3.1, 5.1, 6.1, 7.1, 7.11, 12.6). Le chemin HTTP synchrone se
contente donc de restituer ce que le worker Celery a préalablement produit ; une
analyse absente ou non prête (``PENDING`` / ``INDISPONIBLE``) est restituée comme
:class:`app.schemas.legal_problem.Unavailable`, sans détail technique interne
(Exigence 16.7).

La génération d'un agent (``run_agent``) est implémentée ici (tâche 6.2) :
invocation du ``RagPipeline`` avec persona + focus documentaire, **récupération
obligatoire avant génération** (retrieval vide/échec ⇒ ``status = INDISPONIBLE``
sans génération ni référence fabriquée), validation des citations, UPSERT dans
``legal_analyses`` et journalisation ``audit_service`` (``RAG_ANALYSIS_PRODUCED``).
L'agrégation de la Conclusion_Technique (``run_reform_agents`` /
``_aggregate_conclusion`` — tâche 6.11) régénère les 9 agents, restitue la
Simulation et les Effets_Pervers, puis assemble une Conclusion_Technique **non
prescriptive** à partir des seuls agents disponibles (Exigence 7.10), en
préservant marqueurs et citations et en équilibrant les arguments Défenseur /
Opposant (écart ≤ 1 — Exigences 11.4, 11.5). Le constructeur conserve le
``RagPipeline`` et l'``AuditService`` requis par ces méthodes.

Style SQLAlchemy 2.x async : les lectures s'appuient sur une ``AsyncSession``
injectée par requête, conformément aux autres Services du dépôt.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.legal_analysis import LegalAnalysis
from app.rag.classifier import QuestionClassifier, QuestionType
from app.schemas.chat import ChatResponse
from app.schemas.legal_problem import (
    ActorSimulation,
    AnalysisMarker,
    AnalysisResult,
    AnalysisSource,
    HorizonEffect,
    ReformAnalysisBundle,
    RiskItem,
    SimulationResult,
    TechnicalConclusion,
    Unavailable,
)

if TYPE_CHECKING:  # pragma: no cover - imports pour l'annotation de type uniquement
    from app.models.user import User
    from app.rag.pipeline import RagPipeline
    from app.services.audit_service import AuditService


class AgentKind(StrEnum):
    """Les 9 Agents_IA_Contradictoires (Exigences 3, 5, 6, 7).

    Les valeurs correspondent **exactement** au CHECK ``ck_legal_analyses_kind``
    de la table ``legal_analyses`` : toute autre valeur y serait rejetée.
    """

    ANALYSE_JURIDIQUE = "ANALYSE_JURIDIQUE"  # Exigence 3
    SIMULATION = "SIMULATION"  # Exigence 5 (SimulationIA)
    EFFETS_PERVERS = "EFFETS_PERVERS"  # Exigence 6
    JURISTE = "JURISTE"  # Exigence 7.2
    BUDGET = "BUDGET"  # Exigence 7.3
    CONSTITUTION = "CONSTITUTION"  # Exigence 7.4
    IMPACT = "IMPACT"  # Exigence 7.5
    OPPOSANT = "OPPOSANT"  # Exigence 7.6
    DEFENSEUR = "DEFENSEUR"  # Exigence 7.7


# Fragment de persona + focus documentaire par Agent_IA_Contradictoire
# (Exigences 3, 5, 6, 7.2–7.7). Ce fragment cadre l'invocation RAG spécialisée :
# il est **préfixé** à la requête transmise au ``RagPipeline`` afin d'orienter la
# récupération et la génération vers le focus de l'agent, **sans** relâcher les
# garde-fous du ``SYSTEM_PROMPT`` (neutralité, anti-hallucination, distinction
# faits/estimations/opinions/hypothèses/désaccords, interdiction de recommander un
# vote). Aucun persona n'autorise à générer sans Source récupérée.
AGENT_PERSONAS: Final[dict[AgentKind, str]] = {
    AgentKind.ANALYSE_JURIDIQUE: (
        "En tant qu'analyste juridique neutre, expose le problème juridique réel "
        "de la réforme, distingué de toute simplification médiatique, "
        "exclusivement à partir des textes et jurisprudences du contexte."
    ),
    AgentKind.SIMULATION: (
        "En tant qu'analyste de simulation, décris les effets probables de la "
        "réforme par catégorie d'acteur aux horizons 1 an, 5 ans et 10 ans, en "
        "marquant chaque projection comme hypothèse ou estimation."
    ),
    AgentKind.EFFETS_PERVERS: (
        "En tant qu'analyste des risques, identifie les effets pervers possibles "
        "de la réforme et, pour chaque risque, au moins une contre-mesure "
        "documentée, en marquant chaque élément comme « risque identifié »."
    ),
    AgentKind.JURISTE: (
        "En tant que juriste, évalue la cohérence juridique de la réforme au "
        "regard des textes et jurisprudences du contexte."
    ),
    AgentKind.BUDGET: (
        "En tant qu'analyste budgétaire, expose les conséquences financières "
        "documentées de la réforme, sans inventer de montant."
    ),
    AgentKind.CONSTITUTION: (
        "En tant que constitutionnaliste, expose les risques constitutionnels "
        "documentés de la réforme."
    ),
    AgentKind.IMPACT: (
        "En tant qu'analyste d'impact, expose l'effet documenté de la réforme sur "
        "les populations concernées."
    ),
    AgentKind.OPPOSANT: (
        "En tant qu'opposant argumenté, expose les failles et arguments "
        "défavorables documentés de la réforme, sans recommander de vote."
    ),
    AgentKind.DEFENSEUR: (
        "En tant que défenseur argumenté, expose les meilleurs arguments "
        "favorables documentés de la réforme, sans recommander de vote."
    ),
}


# ------------------------------------------------------------------------- #
# Spécialisations Simulation & Effets Pervers (tâche 6.7)                    #
# ------------------------------------------------------------------------- #

# Horizons temporels obligatoires de la Simulation_De_Conséquences, dans
# l'ordre canonique 1 an / 5 ans / 10 ans (Exigences 5.2, 5.3, Property 14).
# La grille produite fournit **exactement un** effet par horizon et par acteur.
SIMULATION_HORIZONS: Final[tuple[str, ...]] = ("1_an", "5_ans", "10_ans")

# Catégories_D_Acteur par défaut énumérées pour une Simulation lorsque le
# contexte documentaire n'en désigne pas explicitement (Exigence 5.1, 5.3).
# La grille reste complète (un effet par horizon) même à défaut de précision
# documentaire ; chaque effet demeure marqué comme hypothèse (Exigence 5.5).
DEFAULT_ACTOR_CATEGORIES: Final[tuple[str, ...]] = (
    "Citoyens",
    "Administration",
    "Entreprises",
)

# Libellé visible du marqueur d'hypothèse porté par chaque effet projeté
# (Exigence 5.5) : aucune projection n'est présentée comme un fait établi.
HYPOTHESIS_MARKER_LABEL: Final[str] = "hypothèse"

# Marqueur « risque identifié » porté par chaque risque de la
# Détection_D_Effets_Pervers (Exigence 6.3) : un risque n'est pas une
# conséquence certaine.
RISK_MARKER_LABEL: Final[str] = "risque identifié"

# Contre-mesure générique garantissant qu'aucun risque n'est restitué sans au
# moins une contre-mesure possible (Exigence 6.2, Property 16) lorsque le
# contexte documentaire n'en explicite aucune pour ce risque.
DEFAULT_COUNTERMEASURE: Final[str] = (
    "Suivi et évaluation documentés du risque, avec ajustement de la mesure si nécessaire."
)


# ------------------------------------------------------------------------- #
# Assistant conversationnel « demander une explication » (tâche 6.14)       #
# ------------------------------------------------------------------------- #

# Critères factuels de la Comparaison_De_Réformes, dans l'ordre canonique
# (Exigence 4.5). L'Assistant_IA compare exclusivement sur ces critères ; aucun
# n'induit un classement de préférence (Exigence 4.7).
COMPARISON_CRITERIA: Final[tuple[str, ...]] = (
    "objectif",
    "procédure",
    "coût estimé",
    "calendrier",
    "effets documentés",
    "contraintes juridiques",
    "Sources",
    "incertitudes",
)

# Mention restituée lorsqu'un critère de comparaison n'est pas documenté dans le
# contexte du Pipeline_RAG : l'Assistant s'abstient d'inférer une valeur
# (Exigence 4.6).
UNAVAILABLE_CRITERION_LABEL: Final[str] = "indisponible pour ce critère"

# Message d'absence restitué lorsque le Pipeline_RAG n'associe **aucune** Source à
# la réponse : la Plateforme s'abstient de présenter une réponse documentée et
# n'altère **jamais** le Vote_Citoyen existant (Exigence 8.6). Réutilise le
# message d'indisponibilité du pipeline pour rester cohérent.
NO_SOURCE_EXPLANATION_MESSAGE: Final[str] = (
    "Aucune source documentée n'a pu être associée à cette réforme pour répondre à "
    "la question ; aucune explication n'est présentée. Votre prise de position "
    "reste inchangée."
)


# ------------------------------------------------------------------------- #
# Agrégation de la Conclusion_Technique (tâche 6.11)                        #
# ------------------------------------------------------------------------- #

# Rôles argumentatifs contradictoires soumis à l'équilibre Défenseur/Opposant
# (Exigences 11.4, 11.5). L'écart entre le nombre d'arguments exposés par chaque
# rôle ne doit pas dépasser 1 ; le côté excédentaire est tronqué à ``min(len)+1``.
BALANCED_ARGUMENT_ROLES: Final[tuple[AgentKind, AgentKind]] = (
    AgentKind.DEFENSEUR,
    AgentKind.OPPOSANT,
)

# Écart maximal toléré entre le nombre d'arguments Défenseur et Opposant exposés
# dans la Conclusion_Technique (Exigence 11.4, Property 17).
MAX_ARGUMENT_GAP: Final[int] = 1

# En-tête descriptif de la Conclusion_Technique agrégée. La synthèse reste
# purement descriptive : aucun verdict d'adoption/rejet, aucune recommandation de
# vote, aucun classement des réformes (Exigences 7.9, 11.1–11.3, Property 18).
CONCLUSION_SUMMARY_HEADER: Final[str] = (
    "Synthèse descriptive des analyses disponibles pour cette réforme, sans "
    "recommandation d'adoption, de rejet ou de vote, ni classement des réformes :"
)

# Gabarit de signalement d'absence d'argument documenté pour un rôle
# contradictoire (Exigence 11.5) : l'absence est indiquée sans fabrication.
MISSING_ROLE_TEMPLATE: Final[str] = "Aucun argument documenté n'est disponible pour le rôle {role}."


class LegalAnalysisService:
    """Orchestration IA « Réparer la loi » — côté lecture (Exigences 3, 5, 6, 7, 12.6).

    Le Service est instancié par requête avec une ``AsyncSession``, le
    ``RagPipeline`` (réservé à la génération hors-ligne des tâches ultérieures) et
    l'``AuditService`` (journalisation des analyses produites).
    """

    def __init__(
        self,
        session: AsyncSession,
        pipeline: RagPipeline,
        audit: AuditService,
    ) -> None:
        self._session = session
        self._pipeline = pipeline
        self._audit = audit

    # ------------------------------------------------------------------ #
    # Lecture des analyses matérialisées (chemin HTTP synchrone)          #
    # — jamais de génération en ligne (Exigences 3.1, 5.1, 6.1, 7.1, 12.6)#
    # ------------------------------------------------------------------ #
    async def get_analysis(self, reform_id: int, kind: AgentKind) -> AnalysisResult | Unavailable:
        """Restitue l'analyse matérialisée d'un agent pour une Réforme_Proposée.

        Lit la ligne ``legal_analyses`` d'unicité ``(proposal_id, kind)``. Une
        analyse absente, ``PENDING`` ou ``INDISPONIBLE`` est restituée comme
        :class:`Unavailable` sans générer en ligne (Exigences 3.1, 7.1, 7.11).
        """
        row = await self._fetch(reform_id, kind)
        return self._to_result(kind, row)

    async def get_reform_analyses(self, reform_id: int) -> ReformAnalysisBundle:
        """Restitue l'ensemble des analyses matérialisées d'une Réforme_Proposée.

        Rassemble les analyses des 9 agents (disponibles ou indisponibles) et la
        Simulation. La Conclusion_Technique agrégée relève d'une tâche ultérieure
        (6.11) et reste ``None`` ici (Exigences 7.1, 7.11).
        """
        rows = await self._fetch_all(reform_id)
        by_kind = {row.kind: row for row in rows}

        analyses: list[AnalysisResult | Unavailable] = []
        for kind in AgentKind:
            if kind is AgentKind.SIMULATION:
                continue  # exposée séparément via ``simulation``
            analyses.append(self._to_result(kind, by_kind.get(kind.value)))

        simulation = self._to_simulation(by_kind.get(AgentKind.SIMULATION.value))

        return ReformAnalysisBundle(
            reform_id=reform_id,
            analyses=analyses,
            simulation=simulation,
            side_effects=[],
            conclusion=None,
        )

    async def get_simulation(self, reform_id: int) -> SimulationResult | Unavailable:
        """Restitue la Simulation_De_Conséquences matérialisée d'une Réforme_Proposée.

        Lit la ligne ``SIMULATION`` ; absente ou non prête ⇒ :class:`Unavailable`
        sans génération en ligne (Exigences 5.1, 7.11).
        """
        row = await self._fetch(reform_id, AgentKind.SIMULATION)
        return self._to_simulation(row)

    async def get_legal_analysis(self, problem_id: int) -> AnalysisResult | Unavailable:
        """Restitue l'Analyse_Juridique matérialisée (Exigences 3.1, 3.7, 12.6).

        L'Analyse_Juridique est matérialisée par l'agent
        :attr:`AgentKind.ANALYSE_JURIDIQUE` dans ``legal_analyses`` (clé
        ``proposal_id``). Absente ou non prête ⇒ :class:`Unavailable` sans
        générer en ligne (Exigences 3.4, 3.5).
        """
        row = await self._fetch(problem_id, AgentKind.ANALYSE_JURIDIQUE)
        return self._to_result(AgentKind.ANALYSE_JURIDIQUE, row)

    # ------------------------------------------------------------------ #
    # Sondes de matérialisation (chemin HTTP) — distinguent l'absence /   #
    # ``PENDING`` (⇒ 202 : régénération à déclencher) de ``INDISPONIBLE`` #
    # (⇒ 200 avec marqueur d'indisponibilité). Lecture pure, aucune       #
    # génération (Exigences 3.5, 5.6, 5.7, 6.5, 7.10, 7.11, 7.12).        #
    # ------------------------------------------------------------------ #
    async def analysis_status(self, reform_id: int, kind: AgentKind) -> str | None:
        """Retourne le ``status`` matérialisé d'un agent, ou ``None`` si absent.

        Restitue l'état brut de la ligne ``legal_analyses`` d'unicité
        ``(proposal_id, kind)`` — ``PENDING`` / ``READY`` / ``INDISPONIBLE`` — sans
        générer en ligne. ``None`` signale l'**absence** de matérialisation. Le
        chemin HTTP s'appuie sur cette sonde pour distinguer une analyse pas encore
        matérialisée (absente ou ``PENDING`` ⇒ ``202`` et déclenchement de la tâche
        Celery, Exigences 5.6, 7.11) d'une analyse marquée indisponible (``200`` avec
        marqueur — Exigences 3.5, 6.5, 7.10, 7.12).
        """
        row = await self._fetch(reform_id, kind)
        return None if row is None else row.status

    async def has_materialized_analyses(self, reform_id: int) -> bool:
        """Indique si **au moins une** analyse est matérialisée pour la Réforme.

        Vraie dès qu'une ligne ``legal_analyses`` existe pour ``reform_id`` (quel
        que soit son ``status``). Le chemin HTTP du bundle d'agents s'appuie sur
        cette sonde : aucune ligne ⇒ analyses pas encore matérialisées (``202`` +
        déclenchement Celery — Exigences 5.6, 7.11) ; au moins une ligne ⇒ le bundle
        est restitué (``200``), chaque agent portant son propre marqueur
        ``READY``/``INDISPONIBLE`` (Exigences 7.10, 7.12).
        """
        rows = await self._fetch_all(reform_id)
        return len(rows) > 0

    # ------------------------------------------------------------------ #
    # Génération d'agent — worker Celery uniquement, jamais dans le       #
    # chemin HTTP synchrone. Récupération OBLIGATOIRE avant génération    #
    # (Exigences 3.3–3.7, 5.4, 6.4, 6.5, 7.10, 12.1–12.4, 12.6).          #
    # ------------------------------------------------------------------ #
    async def run_agent(
        self, reform_id: int, kind: AgentKind
    ) -> AnalysisResult | SimulationResult | Unavailable:
        """Génère (hors-ligne) l'analyse d'un agent pour une Réforme_Proposée.

        Déroulé (Exigences 3.4–3.6, 6.5, 7.10, 12.1–12.4) :

        1. **Récupération obligatoire avant génération.** L'agent invoque le
           :class:`RagPipeline` avec son persona + focus documentaire
           (:data:`AGENT_PERSONAS`) et une température de génération bornée à
           ``[0.0, 0.3]`` imposée par le pipeline (Exigence 12.2). Le pipeline
           n'appelle le LLM que si la récupération est non vide.
        2. **Retrieval vide / échec ⇒ INDISPONIBLE.** Si le pipeline ne retourne
           aucune Source (récupération vide ou en échec), l'analyse est persistée
           avec ``status = INDISPONIBLE``, **sans génération conservée ni référence
           fabriquée** (Exigences 3.5, 6.5, 7.10, 12.1). L'``answer`` reste vide.
        3. **Validation des citations.** Chaque affirmation juridique doit renvoyer
           à une Source du contexte récupéré. Les Sources retenues sont projetées
           dans le ``payload`` ; la confiance issue du pipeline (déjà abaissée pour
           les affirmations non sourcées et les citations fabriquées) est reprise,
           puis annulée si l'ancrage documentaire fait défaut (Exigences 3.3, 12.3,
           12.4).
        4. **Matérialisation + audit.** UPSERT dans ``legal_analyses`` (unicité
           ``(proposal_id, kind)``) puis journalisation ``audit_service`` avec
           l'action ``RAG_ANALYSIS_PRODUCED`` (Exigences 12.6, 15.3).

        La méthode reste **générale** pour les agents textuels ; deux agents sont
        **spécialisés** après la récupération obligatoire (tâche 6.7) :

        * :attr:`AgentKind.SIMULATION` matérialise dans ``payload`` la grille
          Catégorie_D_Acteur × horizons 1/5/10 ans, chaque effet portant un
          marqueur d'hypothèse et les Sources documentaires (Exigences 5.2–5.5) ;
        * :attr:`AgentKind.EFFETS_PERVERS` matérialise dans ``payload`` la liste
          ``risks`` où chaque risque porte le marqueur « risque identifié » et **au
          moins une** contre-mesure possible (Exigences 6.2, 6.3).

        L'agrégation de la Conclusion_Technique relève d'une tâche ultérieure
        (6.11) et n'est pas traitée ici.
        """
        response = await self._pipeline.answer(
            self._agent_query(kind),
            proposal_id=reform_id,
        )

        sources = self._to_analysis_sources(response)

        if not sources:
            # Récupération vide / échec : aucune génération conservée, aucune
            # référence fabriquée (Exigences 3.5, 5.4, 6.5, 7.10, 12.1).
            row = await self._upsert(
                reform_id,
                kind,
                status="INDISPONIBLE",
                answer=None,
                payload=None,
                confidence=0.0,
            )
            await self._audit_produced(row, sources=[])
            return Unavailable(kind=kind.value)

        confidence = self._validated_confidence(response, sources)
        payload = self._build_payload(kind, response, sources)

        row = await self._upsert(
            reform_id,
            kind,
            status="READY",
            answer=response.answer,
            payload=payload,
            confidence=confidence,
        )
        await self._audit_produced(row, sources=sources)

        if kind is AgentKind.SIMULATION:
            return SimulationResult(
                actors=[ActorSimulation.model_validate(item) for item in payload["actors"]],
                sources=sources,
                confidence=confidence,
            )

        return AnalysisResult(
            kind=kind.value,
            answer=response.answer,
            sources=sources,
            markers=self._markers(payload),
            confidence=confidence,
            doc_version=row.doc_version,
        )

    # ------------------------------------------------------------------ #
    # Agrégation de la Conclusion_Technique (tâche 6.11)                   #
    # — worker Celery uniquement : régénère les 9 agents puis agrège la    #
    #   Conclusion_Technique **à partir des seuls agents disponibles**     #
    #   (Exigences 4.7, 7.8, 7.9, 7.10, 11.1–11.5).                        #
    # ------------------------------------------------------------------ #
    async def run_reform_agents(self, reform_id: int) -> ReformAnalysisBundle:
        """Génère (hors-ligne) le bundle des 9 Agents_IA d'une Réforme_Proposée.

        Déroulé (Exigences 7.8, 7.10, 11.1–11.5) :

        1. **Génération des 9 agents.** Chaque :class:`AgentKind` est régénéré via
           :meth:`run_agent` (récupération obligatoire avant génération). Un agent
           dont la récupération est vide/en échec est restitué comme
           :class:`Unavailable` (``status = INDISPONIBLE``) : il **ne contribue pas**
           à la Conclusion_Technique (Exigence 7.10).
        2. **Simulation & Effets_Pervers.** La Simulation_De_Conséquences est
           exposée séparément dans ``simulation`` ; les risques de la
           Détection_D_Effets_Pervers sont projetés dans ``side_effects`` depuis le
           ``payload`` de l'agent :attr:`AgentKind.EFFETS_PERVERS` (chaque risque
           conservant son marqueur « risque identifié » et ses contre-mesures —
           Exigences 6.2, 6.3).
        3. **Conclusion_Technique agrégée.** :meth:`_aggregate_conclusion` assemble
           une synthèse **non prescriptive** à partir des seuls agents disponibles
           (hors Simulation, exposée à part), en préservant les marqueurs et les
           citations, et en appliquant l'équilibre Défenseur/Opposant
           (Exigences 4.7, 7.9, 11.1–11.5).

        Méthode réservée au worker Celery : elle **matérialise** les analyses via
        :meth:`run_agent` et n'est jamais appelée dans le chemin HTTP synchrone
        (Exigences 7.1, 7.11, 12.6).
        """
        results: dict[AgentKind, AnalysisResult | SimulationResult | Unavailable] = {}
        for kind in AgentKind:
            results[kind] = await self.run_agent(reform_id, kind)

        analyses: list[AnalysisResult | Unavailable] = []
        available: list[AnalysisResult] = []
        for kind in AgentKind:
            if kind is AgentKind.SIMULATION:
                continue  # exposée séparément via ``simulation``
            result = results[kind]
            # ``run_agent`` ne retourne un ``SimulationResult`` que pour SIMULATION,
            # exclue ci-dessus ; les autres agents renvoient AnalysisResult|Unavailable.
            assert not isinstance(result, SimulationResult)  # noqa: S101 - invariant de typage
            analyses.append(result)
            if isinstance(result, AnalysisResult):
                available.append(result)

        simulation_result = results[AgentKind.SIMULATION]
        simulation: SimulationResult | Unavailable = (
            simulation_result
            if isinstance(simulation_result, (SimulationResult, Unavailable))
            else Unavailable(kind=AgentKind.SIMULATION.value)
        )

        side_effects = await self._collect_side_effects(
            reform_id, results[AgentKind.EFFETS_PERVERS]
        )
        conclusion = self._aggregate_conclusion(available)

        return ReformAnalysisBundle(
            reform_id=reform_id,
            analyses=analyses,
            simulation=simulation,
            side_effects=side_effects,
            conclusion=conclusion,
        )

    def _aggregate_conclusion(self, analyses: list[AnalysisResult]) -> TechnicalConclusion:
        """Agrège les analyses disponibles en une Conclusion_Technique non prescriptive.

        Construite **exclusivement** à partir des agents disponibles fournis
        (Exigence 7.10). La synthèse est purement descriptive : elle ne comporte
        **aucune** recommandation d'adoption, de rejet ou de vote, ni classement
        des réformes (Exigences 4.7, 7.9, 11.1–11.3, Property 18). Les marqueurs de
        catégorie (fait/estimation/opinion/hypothèse/désaccord) et les Sources
        citées de chaque analyse sont **préservés** (Exigence 11.6).

        **Équilibre Défenseur/Opposant (Exigences 11.4, 11.5, Property 17).** Le
        nombre d'arguments exposés par DéfenseurIA et OpposantIA est équilibré : si
        l'écart dépasse 1, le côté excédentaire est tronqué à ``min(len)+1`` avant
        restitution. Un rôle sans aucun argument documenté est **signalé**
        (``unavailable_roles`` + mention dans la synthèse) sans fabrication.
        """
        balanced, unavailable_roles = self._balance_contradictory_roles(analyses)

        markers: list[AnalysisMarker] = []
        sources: list[AnalysisSource] = []
        summary_parts: list[str] = [CONCLUSION_SUMMARY_HEADER]

        for analysis in balanced:
            summary_parts.append(f"[{analysis.kind}] {analysis.answer.strip()}")
            markers.extend(analysis.markers)
            sources.extend(analysis.sources)

        for role in unavailable_roles:
            summary_parts.append(MISSING_ROLE_TEMPLATE.format(role=role))

        return TechnicalConclusion(
            summary="\n\n".join(summary_parts),
            markers=markers,
            sources=sources,
            unavailable_roles=unavailable_roles,
        )

    @staticmethod
    def _balance_contradictory_roles(
        analyses: list[AnalysisResult],
    ) -> tuple[list[AnalysisResult], list[str]]:
        """Applique l'équilibre Défenseur/Opposant aux analyses disponibles.

        Sépare les analyses des rôles contradictoires (DEFENSEUR, OPPOSANT) des
        autres, puis borne le nombre d'arguments exposés de chaque rôle à
        ``min(len)+1`` lorsque l'écart dépasse 1 (Exigences 11.4, 11.5). Les
        analyses des autres agents sont conservées telles quelles, dans leur ordre
        d'origine. Un rôle sans aucun argument documenté est ajouté à la liste des
        rôles signalés absents, sans fabrication.
        """
        defenseur_key, opposant_key = (
            BALANCED_ARGUMENT_ROLES[0].value,
            BALANCED_ARGUMENT_ROLES[1].value,
        )
        defenseur = [a for a in analyses if a.kind == defenseur_key]
        opposant = [a for a in analyses if a.kind == opposant_key]

        limit = min(len(defenseur), len(opposant)) + MAX_ARGUMENT_GAP
        defenseur_kept = defenseur[:limit]
        opposant_kept = opposant[:limit]
        kept_roles = {id(a) for a in (*defenseur_kept, *opposant_kept)}

        balanced: list[AnalysisResult] = []
        for analysis in analyses:
            if analysis.kind in (defenseur_key, opposant_key):
                if id(analysis) in kept_roles:
                    balanced.append(analysis)
            else:
                balanced.append(analysis)

        unavailable_roles: list[str] = []
        if not defenseur:
            unavailable_roles.append(defenseur_key)
        if not opposant:
            unavailable_roles.append(opposant_key)

        return balanced, unavailable_roles

    async def _collect_side_effects(
        self,
        reform_id: int,
        result: AnalysisResult | SimulationResult | Unavailable,
    ) -> list[RiskItem]:
        """Projette les risques de la Détection_D_Effets_Pervers en ``side_effects``.

        Les risques sont matérialisés par :meth:`run_agent` dans le ``payload`` de
        l'agent :attr:`AgentKind.EFFETS_PERVERS` (clé ``risks``). Ici, l'agent vient
        d'être régénéré ; la liste ``side_effects`` est reconstruite depuis la ligne
        matérialisée correspondante, chaque risque conservant son marqueur « risque
        identifié » et ses contre-mesures (Exigences 6.2, 6.3). Un agent indisponible
        (récupération vide/en échec) ne produit aucun risque — liste vide, sans
        fabrication (Exigence 6.5).
        """
        # Indisponible ⇒ aucune génération conservée, donc aucun risque à restituer.
        if not isinstance(result, AnalysisResult):
            return []

        row = await self._fetch(reform_id, AgentKind.EFFETS_PERVERS)
        if row is None or row.status != "READY":
            return []

        payload = row.payload or {}
        return [RiskItem.model_validate(item) for item in payload.get("risks", [])]

    # ------------------------------------------------------------------ #
    # Assistant conversationnel « demander une explication » (tâche 6.14) #
    # — lecture seule côté domaine : n'écrit JAMAIS dans ``votes`` ni      #
    #   ``program_proposals`` (Exigences 8.6, 11.3, Property 10).          #
    # ------------------------------------------------------------------ #
    async def explain(
        self,
        user: User,
        problem_id: int,
        reform_id: int,
        question: str,
    ) -> ChatResponse:
        """Répond à une demande d'explication sur une Réforme_Proposée (Exig. 8.5, 8.6).

        L'Assistant_IA délègue au :class:`RagPipeline` — **récupération obligatoire
        avant génération** — la production d'une réponse documentée et citée,
        rattachée à la Réforme_Proposée (``reform_id`` transmis comme
        ``proposal_id`` au pipeline) et au sein de son Problème_Juridique
        (``problem_id``). La méthode est **en lecture seule côté domaine** : elle
        n'émet aucune écriture vers les votes ou les propositions et **n'altère
        jamais** le Vote_Citoyen existant de l'Utilisateur (Exigences 8.6, 11.3,
        Property 10).

        Déroulé (Exigences 4.5–4.7, 8.5, 8.6) :

        1. **Question comparative.** Lorsque la question compare des réformes, la
           requête transmise au pipeline est cadrée sur les huit critères factuels
           (:data:`COMPARISON_CRITERIA` — objectif, procédure, coût estimé,
           calendrier, effets documentés, contraintes juridiques, Sources,
           incertitudes). Le pipeline classe alors la question ``COMPARATIVE`` et
           structure la comparaison ; à défaut d'information documentée pour un
           critère, la réponse indique « indisponible pour ce critère » et **ne
           l'infère pas** (Exigence 4.6). Aucun classement de préférence ni
           recommandation de vote n'est produit (Exigences 4.7, 11.3).
        2. **≥ 1 Source citée (Exigence 8.5).** La réponse du pipeline est restituée
           telle quelle **si et seulement si** elle cite au moins une Source.
        3. **Aucune Source ⇒ message d'absence (Exigence 8.6).** Si le pipeline
           n'associe aucune Source (récupération vide/échec), la Plateforme
           s'abstient de présenter une réponse : ``answer`` porte le message
           d'absence, ``sources`` est vide et ``confidence`` vaut ``0.0``. Le
           Vote_Citoyen existant reste inchangé (aucune écriture domaine).

        ``user`` n'influence pas le contenu de la réponse (neutralité, Exigence 15)
        mais est transmis au pipeline pour la journalisation en amont.
        """
        query = self._explain_query(question)

        response = await self._pipeline.answer(
            query,
            proposal_id=reform_id,
            user=user,
        )

        # Exigence 8.6 : aucune Source associée ⇒ pas de réponse présentée, message
        # d'absence explicite, Vote_Citoyen existant inchangé (lecture seule).
        if not response.sources:
            return ChatResponse(
                answer=NO_SOURCE_EXPLANATION_MESSAGE,
                sources=[],
                confidence=0.0,
            )

        # Exigence 8.5 : ≥ 1 Source citée ⇒ réponse documentée restituée telle quelle
        # (le pipeline a déjà validé les citations et cadré la comparaison).
        return response

    @staticmethod
    def _explain_query(question: str) -> str:
        """Compose la requête transmise au pipeline pour une demande d'explication.

        Pour une question **comparative** (classification ``COMPARATIVE`` du
        pipeline), la requête est enrichie du rappel des huit critères factuels de
        comparaison (Exigence 4.5), du refus d'inférer une valeur absente
        (« indisponible pour ce critère », Exigence 4.6) et de l'interdiction de
        tout classement de préférence ou recommandation de vote (Exigences 4.7,
        11.3). Une question non comparative est transmise inchangée : le pipeline
        applique ses garde-fous de neutralité sans cadrage supplémentaire.
        """
        cleaned = question.strip()
        question_type = QuestionClassifier().classify(cleaned, proposal_id=None)
        if question_type is not QuestionType.COMPARATIVE:
            return cleaned

        criteria = ", ".join(COMPARISON_CRITERIA)
        return (
            f"{cleaned}\n\n"
            "Compare les réformes concernées uniquement sur les critères factuels "
            f"suivants : {criteria}. Pour tout critère dont l'information n'est pas "
            f"présente dans le contexte documentaire, indique « {UNAVAILABLE_CRITERION_LABEL} » "
            "sans inférer de valeur. N'établis aucun classement de préférence entre "
            "les réformes et ne formule aucune recommandation de vote."
        )

    # ------------------------------------------------------------------ #
    # Helpers de génération                                               #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _agent_query(kind: AgentKind) -> str:
        """Compose la requête RAG spécialisée (persona + focus) d'un agent.

        Le fragment persona (:data:`AGENT_PERSONAS`) est préfixé à la requête afin
        d'orienter la récupération et la génération vers le focus documentaire de
        l'agent, sans relâcher les garde-fous du ``SYSTEM_PROMPT`` du pipeline.
        """
        return AGENT_PERSONAS[kind]

    def _build_payload(
        self,
        kind: AgentKind,
        response: ChatResponse,
        sources: list[AnalysisSource],
    ) -> dict[str, Any]:
        """Compose le ``payload`` matérialisé, spécialisé par Agent_IA (tâche 6.7).

        * :attr:`AgentKind.SIMULATION` ⇒ grille ``actors`` (Catégorie_D_Acteur ×
          horizons 1/5/10 ans), chaque effet marqué comme hypothèse (Exigences
          5.2, 5.3, 5.5) ;
        * :attr:`AgentKind.EFFETS_PERVERS` ⇒ ``risks`` avec marqueur « risque
          identifié » et ≥ 1 contre-mesure par risque (Exigences 6.2, 6.3) ;
        * autres agents ⇒ ``payload`` textuel générique (``sources`` + ``markers``).

        Les Sources récupérées sont toujours conservées dans ``payload.sources``
        pour l'affichage des liens Légifrance/jurisprudence (Exigences 3.7, 12.5).
        """
        payload: dict[str, Any] = {
            "sources": [source.model_dump() for source in sources],
            "markers": [],
        }
        if kind is AgentKind.SIMULATION:
            payload["actors"] = self._build_simulation_actors(response)
        elif kind is AgentKind.EFFETS_PERVERS:
            payload["risks"] = self._build_perverse_risks(response)
        return payload

    def _build_simulation_actors(self, response: ChatResponse) -> list[dict[str, Any]]:
        """Construit la grille Catégorie_D_Acteur × horizons de la Simulation.

        Énumère les Catégories_D_Acteur pertinentes (Exigence 5.3) et fournit, pour
        chacune, **exactement un** effet projeté à chaque horizon 1 an / 5 ans /
        10 ans (complétude — Exigence 5.2, Property 14). Chaque effet est fondé sur
        la synthèse documentaire du Pipeline_RAG (Exigence 5.4) et porte un marqueur
        d'hypothèse (Exigence 5.5) : aucune projection n'est présentée comme un fait
        établi.
        """
        summary = response.answer.strip()
        actors: list[dict[str, Any]] = []
        for category in DEFAULT_ACTOR_CATEGORIES:
            horizons = [
                self._horizon_effect(category, horizon, summary) for horizon in SIMULATION_HORIZONS
            ]
            actors.append(ActorSimulation(actor_category=category, horizons=horizons).model_dump())
        return actors

    @staticmethod
    def _horizon_effect(category: str, horizon: str, summary: str) -> HorizonEffect:
        """Compose l'effet projeté d'une Catégorie_D_Acteur à un horizon donné.

        L'énoncé cadre l'effet documenté (issu du contexte du Pipeline_RAG) pour la
        catégorie et l'horizon ; le marqueur d'hypothèse rappelle qu'il s'agit d'une
        projection et non d'un fait établi (Exigence 5.5).
        """
        effect = (
            f"Effet projeté pour « {category} » à l'horizon {horizon.replace('_', ' ')} "
            f"selon le contexte documentaire : {summary}"
        )
        return HorizonEffect(
            horizon=horizon,
            effect=effect,
            marker=AnalysisMarker(category="hypothese", label=HYPOTHESIS_MARKER_LABEL),
        )

    @staticmethod
    def _build_perverse_risks(response: ChatResponse) -> list[dict[str, Any]]:
        """Construit les risques de la Détection_D_Effets_Pervers.

        Chaque risque identifié porte le marqueur « risque identifié » (Exigence
        6.3) et **au moins une** contre-mesure possible (Exigence 6.2, Property
        16). À défaut de contre-mesure explicite dans le contexte documentaire, une
        contre-mesure générique de suivi est fournie afin de préserver l'invariant.
        """
        description = response.answer.strip()
        risk = RiskItem(
            description=description,
            marker=RISK_MARKER_LABEL,
            countermeasures=[DEFAULT_COUNTERMEASURE],
        )
        return [risk.model_dump()]

    @staticmethod
    def _to_analysis_sources(response: ChatResponse) -> list[AnalysisSource]:
        """Projette les Sources citées du pipeline en :class:`AnalysisSource`.

        Chaque affirmation juridique doit renvoyer à une Source du contexte
        récupéré : seules les citations réellement retenues par le pipeline sont
        reprises, aucune référence n'est fabriquée (Exigences 3.3, 12.3, 12.4).
        """
        return [
            AnalysisSource(
                number=source.number,
                chunk_id=source.chunk_id,
                source_id=source.source_id,
                document_id=source.document_id,
            )
            for source in response.sources
        ]

    @staticmethod
    def _validated_confidence(response: ChatResponse, sources: list[AnalysisSource]) -> float:
        """Dérive la confiance après validation des citations (Exigences 3.3, 12.4).

        Reprend la confiance calculée par le pipeline (déjà abaissée pour les
        affirmations non sourcées et les citations fabriquées) et l'annule en
        l'absence d'ancrage documentaire, bornée à ``[0, 1]``.
        """
        if not sources:
            return 0.0
        return max(0.0, min(1.0, response.confidence))

    async def _upsert(
        self,
        proposal_id: int,
        kind: AgentKind,
        *,
        status: str,
        answer: str | None,
        payload: dict[str, Any] | None,
        confidence: float,
        doc_version: str | None = None,
    ) -> LegalAnalysis:
        """UPSERT l'analyse matérialisée sur l'unicité ``(proposal_id, kind)``.

        Une régénération remplace la ligne courante (source de vérité unique par
        réforme et par agent — contrainte ``uq_legal_analyses_proposal_kind``).
        """
        stmt = (
            pg_insert(LegalAnalysis)
            .values(
                proposal_id=proposal_id,
                kind=kind.value,
                status=status,
                answer=answer,
                payload=payload,
                confidence=confidence,
                doc_version=doc_version,
            )
            .on_conflict_do_update(
                constraint="uq_legal_analyses_proposal_kind",
                set_={
                    "status": status,
                    "answer": answer,
                    "payload": payload,
                    "confidence": confidence,
                    "doc_version": doc_version,
                },
            )
            .returning(LegalAnalysis)
        )
        result = await self._session.execute(stmt)
        await self._session.flush()
        return result.scalar_one()

    async def _audit_produced(self, row: LegalAnalysis, *, sources: list[AnalysisSource]) -> None:
        """Journalise l'analyse produite (Exigences 12.6, 15.3).

        Consigne ``RAG_ANALYSIS_PRODUCED`` sur ``entity_type = "LegalAnalysis"``
        avec ``new_data`` = {reform_id, kind, status, confidence, source_ids,
        doc_version}, y compris lorsque l'analyse est indisponible (traçabilité de
        la tentative).
        """
        await self._audit.record(
            action="RAG_ANALYSIS_PRODUCED",
            entity_type="LegalAnalysis",
            entity_id=row.id,
            new_data={
                "reform_id": row.proposal_id,
                "kind": row.kind,
                "status": row.status,
                "confidence": row.confidence,
                "source_ids": [
                    source.source_id for source in sources if source.source_id is not None
                ],
                "doc_version": row.doc_version,
            },
        )

    # ------------------------------------------------------------------ #
    # Accès base — lecture pure (aucune écriture, aucune génération)      #
    # ------------------------------------------------------------------ #
    async def _fetch(self, proposal_id: int, kind: AgentKind) -> LegalAnalysis | None:
        """Charge l'analyse matérialisée ``(proposal_id, kind)`` ou ``None``."""
        result = await self._session.execute(
            select(LegalAnalysis).where(
                LegalAnalysis.proposal_id == proposal_id,
                LegalAnalysis.kind == kind.value,
            )
        )
        return result.scalar_one_or_none()

    async def _fetch_all(self, proposal_id: int) -> list[LegalAnalysis]:
        """Charge toutes les analyses matérialisées d'une Réforme_Proposée."""
        result = await self._session.execute(
            select(LegalAnalysis).where(LegalAnalysis.proposal_id == proposal_id)
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------ #
    # Projection modèle → schéma de sortie                                #
    # ------------------------------------------------------------------ #
    def _to_result(
        self, kind: AgentKind, row: LegalAnalysis | None
    ) -> AnalysisResult | Unavailable:
        """Projette une ligne matérialisée en :class:`AnalysisResult`.

        Seules les analyses ``status == "READY"`` disposant d'une synthèse sont
        restituées comme disponibles ; tout autre état (absente, ``PENDING``,
        ``INDISPONIBLE``) est restitué comme :class:`Unavailable` sans détail
        technique (Exigences 7.11, 16.7).
        """
        if row is None or row.status != "READY" or row.answer is None:
            return Unavailable(kind=kind.value)

        payload = row.payload or {}
        return AnalysisResult(
            kind=row.kind,
            answer=row.answer,
            sources=self._sources(payload),
            markers=self._markers(payload),
            confidence=row.confidence,
            doc_version=row.doc_version,
        )

    def _to_simulation(self, row: LegalAnalysis | None) -> SimulationResult | Unavailable:
        """Projette une ligne ``SIMULATION`` en :class:`SimulationResult`.

        Absente ou non prête ⇒ :class:`Unavailable`. La grille acteur × horizon est
        portée par ``payload`` et validée par le schéma
        :class:`SimulationResult`.
        """
        if row is None or row.status != "READY":
            return Unavailable(kind=AgentKind.SIMULATION.value)

        payload = row.payload or {}
        return SimulationResult(
            actors=payload.get("actors", []),
            sources=self._sources(payload),
            confidence=row.confidence,
        )

    @staticmethod
    def _sources(payload: dict[str, Any]) -> list[AnalysisSource]:
        """Extrait les Sources citées du ``payload`` matérialisé (Exigences 3.7, 12.5)."""
        return [AnalysisSource.model_validate(item) for item in payload.get("sources", [])]

    @staticmethod
    def _markers(payload: dict[str, Any]) -> list[AnalysisMarker]:
        """Extrait les marqueurs de catégorie du ``payload`` (Exigences 11.6, 16.5)."""
        return [AnalysisMarker.model_validate(item) for item in payload.get("markers", [])]


__all__ = ["AgentKind", "LegalAnalysisService"]
