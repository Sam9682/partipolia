"""API REST « Réparer la loi » (Exigences 1, 4, 9, 10, 14).

Routeur monté sous ``/api/v1/legal-problems`` (Exigence 33.1). Cette tâche
n'expose que les **points d'accès publics de lecture** (aucune authentification —
Exigences 1.7, 16.6) :

* ``GET /legal-problems`` — liste paginée bornée des Problèmes_Juridiques
  (``page``, ``limit`` ≤ 100, ``total``) (Exigences 1.4, 14.1) → ``200`` ;
* ``GET /legal-problems/{id}`` — détail d'un Problème_Juridique, Réformes
  comprises (Exigences 1.5, 4.4) → ``200``/``404`` ;
* ``GET /legal-problems/{id}/reforms`` — Réformes_Proposées rattachées, statu
  quo signalé (Exigence 4.4) → ``200``/``404`` ;
* ``GET /legal-problems/{id}/reforms/{reform_id}/votes`` — décomptes
  support/oppose/participation (Exigences 9.1, 9.4, 9.5) → ``200``/``404`` ;
* ``GET /legal-problems/{id}/reforms/{reform_id}/versions`` — historique
  ``ProposalVersion`` par version **croissante** (Exigences 10.4, 10.5)
  → ``200``/``404``.

Toute ressource absente (Problème_Juridique, Réforme_Proposée ou lien
d'association) est mappée sur ``404`` sans divulguer de détail interne
(Exigences 1.6, 9.5, 10.5, 14.2, 14.3) : la
:class:`~app.services.legal_reform_service.LegalProblemNotFoundError` levée par le
Service est convertie ici en :class:`fastapi.HTTPException` ``404``. Toute la
logique métier (pagination bornée, délégation des décomptes et de l'historique)
est déléguée au :class:`~app.services.legal_reform_service.LegalReformService`.

S'ajoutent les **points d'accès d'analyse IA** (lecture des analyses
**matérialisées** par le Worker_Celery — jamais de génération en ligne ;
Exigences 3.5, 5.6, 5.7, 6.5, 7.10, 7.11, 7.12) :

* ``GET /legal-problems/{id}/analysis`` — Analyse_Juridique du droit actuel
  → ``200``/``202``/``404`` ;
* ``GET /legal-problems/{id}/reforms/{reform_id}/simulation`` —
  Simulation_De_Conséquences (1/5/10 ans) → ``200``/``202``/``404`` ;
* ``GET /legal-problems/{id}/reforms/{reform_id}/analysis`` —
  Agents_IA_Contradictoires + Conclusion_Technique → ``200``/``202``/``404``.

Sémantique de statut (REST API Design) : ``200`` si l'analyse est matérialisée
(``READY``) ; ``202`` si elle n'est pas encore matérialisée (absente ou
``PENDING``), auquel cas la tâche Celery ``analyze_reform`` est déclenchée **sans
bloquer** l'API (Exigence 23.3) ; ``200`` avec marqueur d'indisponibilité si une
ligne existe marquée ``INDISPONIBLE`` (retrieval vide/échec, aucune régénération
relancée) ; ``404`` si la ressource est absente.

S'ajoutent enfin les **points d'accès authentifiés d'écriture** (réservés aux
Utilisateurs authentifiés via :func:`app.api.deps.get_current_user` — ``401``
sinon, sans modifier l'état, y compris un Vote_Citoyen existant ; Exigences 8.10,
14.4) :

* ``POST /legal-problems/{id}/reforms/{reform_id}/votes`` — Vote_Citoyen UPSERT
  (``value ∈ {-1, 0, +1}``) → ``200``/``401``/``404``/``422`` ;
* ``POST /legal-problems/{id}/reforms/{reform_id}/positions`` — prise de position
  ``FOR``/``AGAINST`` → ``201``/``401``/``404``/``422`` ;
* ``POST /legal-problems/{id}/reforms/{reform_id}/amendments`` — Amendement
  (→ nouvelle ``ProposalVersion``) → ``201``/``401``/``404``/``422`` ;
* ``POST /legal-problems/{id}/reforms/{reform_id}/side-effects`` —
  Signalement_D_Effet_Secondaire (→ modération) → ``201``/``401``/``404``/``422`` ;
* ``POST /legal-problems/{id}/reforms/{reform_id}/explain`` — « Demander une
  explication » (RAG cité, limitation de débit renforcée) →
  ``200``/``401``/``404``/``422``.

Un corps invalide est rejeté en ``422`` par les schémas de requête (énumération
des champs en erreur ; ``value ∉ {-1,0,+1}``, ``position ∉ {FOR, AGAINST}``,
longueur > 5000 — Exigences 8.9, 13.4, 14.5). Les erreurs métier
:class:`ReformCardinalityError`, :class:`ContributionLengthError` (⇒ ``422``) et
:class:`InvalidArgumentPositionError` (⇒ ``422``) sont mappées sur ``422`` ; toute
ressource absente reste mappée sur ``404`` via :func:`_ensure_reform_link`. Le
modèle d'erreur JSON normalisé et la limitation de débit (``default_rate_limit``)
sont ceux réutilisés par les autres routeurs.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Path, Query, Response, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.api.v1.chat import get_rag_pipeline
from app.core.database import get_session
from app.core.rate_limit import default_rate_limit, rate_limiter_dependency
from app.rag.pipeline import RagPipeline
from app.schemas.argument import ArgumentPublic
from app.schemas.auth import UserPublic
from app.schemas.chat import ChatResponse
from app.schemas.common import Page
from app.schemas.legal_problem import (
    AmendmentInput,
    AnalysisResult,
    ExplainInput,
    LegalProblemDetail,
    LegalProblemSummary,
    PositionInput,
    ReformAnalysisBundle,
    ReformView,
    SideEffectInput,
    SimulationResult,
    Unavailable,
    VoteCounts,
    VoteInput,
)
from app.schemas.proposal import ProposalVersionInfo
from app.services.argument_service import InvalidArgumentPositionError
from app.services.audit_service import AuditService
from app.services.legal_analysis_service import AgentKind, LegalAnalysisService
from app.services.legal_reform_service import (
    DEFAULT_PAGE_LIMIT,
    MAX_PAGE_LIMIT,
    ContributionLengthError,
    LegalProblemNotFoundError,
    LegalReformService,
    ReformCardinalityError,
)

router = APIRouter()

# Message générique renvoyé en 404 (aucun détail interne — Exigences 14.2, 14.3).
_NOT_FOUND_MESSAGE = "Ressource introuvable."

# Statut matérialisé d'une analyse indisponible : une ligne existe, marquée
# « indisponible », restituée en ``200`` avec son marqueur (Exigences 3.5, 6.5,
# 7.10, 7.12), à distinguer de l'absence / ``PENDING`` (⇒ ``202``).
_STATUS_INDISPONIBLE = "INDISPONIBLE"

# Message générique pour une position hors du domaine ``{FOR, AGAINST}`` (Exigence 13.4).
_INVALID_POSITION_MESSAGE = "La position doit être FOR ou AGAINST."

# Limitation de débit renforcée du point d'accès d'explication (RAG cité), aligné
# sur la protection accrue du chat (Exigences 8.5, 8.6, 26.5).
_legal_explain_rate_limit = rate_limiter_dependency("legal-explain", reinforced=True)


class SideEffectReportInfo(BaseModel):
    """Vue de sortie d'un Signalement_D_Effet_Secondaire (Exigences 8.8, 15.1).

    Projette l'entité :class:`~app.models.side_effect_report.SideEffectReport`
    créée puis modérée : le ``status`` restitué reflète la décision du
    Moteur_De_Modération (``VISIBLE`` si acceptable, ``PENDING`` si douteux — sans
    publication tant qu'il n'est pas validé — Exigence 15.1).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="Identifiant du Signalement_D_Effet_Secondaire.")
    proposal_id: int = Field(description="Réforme_Proposée (Proposal) rattachée.")
    author_id: int = Field(description="Auteur du Signalement.")
    content: str = Field(description="Contenu signalé (1..5000 caractères).")
    status: str = Field(description="Statut de modération : PENDING, VISIBLE ou REJECTED.")


def get_legal_reform_service(
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LegalReformService:
    """Dépendance fournissant un :class:`LegalReformService` lié à la session de requête."""
    return LegalReformService(session)


def get_legal_analysis_service(
    session: Annotated[AsyncSession, Depends(get_session)],
    pipeline: Annotated[RagPipeline, Depends(get_rag_pipeline)],
) -> LegalAnalysisService:
    """Dépendance fournissant un :class:`LegalAnalysisService` lié à la requête.

    Réutilise l'assemblage du :class:`RagPipeline` du chemin de chat
    (:func:`app.api.v1.chat.get_rag_pipeline` — recherche hybride + providers
    configurés) et l'associe à l':class:`AuditService` de la session courante,
    conformément à l'assemblage du worker Celery. Sur le chemin HTTP synchrone, le
    Service est utilisé en **lecture seule** : seules les analyses **matérialisées**
    sont restituées, jamais générées en ligne (Exigences 7.1, 7.11, 12.6).
    """
    return LegalAnalysisService(session, pipeline, AuditService(session))


class AnalysisTaskDispatcher(Protocol):
    """Port d'émission de la tâche d'analyse (isole Celery de l'API).

    Le contrat imite l'appel ``analyze_reform.delay(reform_id)`` : il **émet** la
    tâche sans l'exécuter ni bloquer, et retourne un identifiant de tâche (ou
    ``None`` si le back-end ne fournit pas d'``id``).
    """

    def __call__(self, reform_id: int) -> str | None: ...


def _dispatch_analyze_reform(reform_id: int) -> str | None:
    """Émet la tâche Celery ``analyze_reform`` sans bloquer l'API (Exigences 12.1, 23.3).

    L'import de la tâche est **différé** au moment de l'appel afin que ce module
    reste importable dans un environnement de test hors-ligne dépourvu de Celery ou
    d'un broker Redis configuré. En production, ``.delay(...)`` place le message sur
    le broker et rend la main immédiatement (aucune génération dans le chemin de
    requête — Exigences 7.1, 12.1).
    """
    from app.workers.legal_analysis import analyze_reform

    async_result = analyze_reform.delay(reform_id)
    return getattr(async_result, "id", None)


def get_analysis_task_dispatcher() -> AnalysisTaskDispatcher:
    """Dépendance fournissant l'émetteur de la tâche d'analyse.

    Surchargeable en test (``app.dependency_overrides``) par un émetteur factice
    afin de vérifier le câblage HTTP (``202`` + déclenchement) sans Celery ni Redis.
    """
    return _dispatch_analyze_reform


def _not_found() -> HTTPException:
    """Construit l'exception ``404`` normalisée (aucun détail interne)."""
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND_MESSAGE)


@router.get(
    "",
    response_model=Page[LegalProblemSummary],
    summary="Lister les Problèmes_Juridiques (page, limit≤100, total)",
)
async def list_legal_problems(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    page: Annotated[int, Query(ge=1, description="Numéro de page (Exigence 1.4).")] = 1,
    limit: Annotated[
        int,
        Query(
            ge=1,
            le=MAX_PAGE_LIMIT,
            description="Taille de page (défaut 20, max 100 — Exigences 1.4, 14.1).",
        ),
    ] = DEFAULT_PAGE_LIMIT,
) -> Page[LegalProblemSummary]:
    """Retourne la liste paginée bornée des Problèmes_Juridiques (Exigences 1.4, 14.1).

    Consultation **publique** (aucune authentification requise — Exigence 1.7).
    L'enveloppe :class:`Page` expose ``total``, le nombre total de
    Problèmes_Juridiques (Exigence 14.1).
    """
    return await service.list_problems(page=page, limit=limit)


@router.get(
    "/{problem_id}",
    response_model=LegalProblemDetail,
    summary="Détail d'un Problème_Juridique",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Problème_Juridique introuvable"},
    },
)
async def get_legal_problem(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
) -> LegalProblemDetail:
    """Retourne le détail d'un Problème_Juridique, Réformes comprises (Exigences 1.5, 4.4).

    Consultation **publique** ; ``404`` si le Problème_Juridique n'existe pas
    (Exigences 1.6, 14.3).
    """
    try:
        return await service.get_problem(problem_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc


@router.get(
    "/{problem_id}/reforms",
    response_model=list[ReformView],
    summary="Réformes_Proposées d'un Problème_Juridique (statu quo signalé)",
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Problème_Juridique introuvable"},
    },
)
async def list_legal_problem_reforms(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
) -> list[ReformView]:
    """Liste les Réformes_Proposées rattachées, l'Option_Statu_Quo signalée (Exigence 4.4).

    Consultation **publique** ; ``404`` si le Problème_Juridique n'existe pas
    (Exigences 1.6, 14.3). Chaque Réforme porte son marqueur ``is_status_quo``
    (au plus une par problème — Exigence 4.3).
    """
    try:
        return await service.list_reforms(problem_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc


@router.get(
    "/{problem_id}/reforms/{reform_id}/votes",
    response_model=VoteCounts,
    summary="Décomptes support/oppose/participation d'une Réforme_Proposée",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
    },
)
async def get_reform_vote_counts(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
) -> VoteCounts:
    """Retourne les décomptes d'une Réforme_Proposée (Exigences 9.1, 9.4).

    Consultation **publique** ; ``participation_count = support_count +
    oppose_count`` (Votes NEUTRE exclus — Exigence 9.1). La Réforme doit être
    rattachée au Problème_Juridique : ``404`` sinon (Exigences 9.5, 14.3).
    """
    try:
        return await service.reform_vote_counts(problem_id, reform_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc


@router.get(
    "/{problem_id}/reforms/{reform_id}/versions",
    response_model=list[ProposalVersionInfo],
    summary="Historique des Versions_De_Proposition (version croissante)",
    responses={
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
    },
)
async def list_reform_versions(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
) -> list[ProposalVersionInfo]:
    """Liste l'historique des Versions_De_Proposition par version croissante (Exigence 10.4).

    Consultation **publique** ; l'historique est append-only et jamais écrasé
    (Exigence 13.5). La Réforme doit être rattachée au Problème_Juridique :
    ``404`` sinon (Exigences 10.5, 14.3).
    """
    try:
        versions = await service.list_versions(problem_id, reform_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc
    return [ProposalVersionInfo.model_validate(version) for version in versions]


# --------------------------------------------------------------------------- #
# Points d'accès d'analyse IA (matérialisation en lecture + 202)              #
#                                                                             #
# Consultation **publique** (aucune authentification — Exigences 1.7, 16.6).  #
# L'API ne génère **jamais** en ligne : elle restitue les analyses            #
# **matérialisées** par le Worker_Celery (Exigences 7.1, 7.11, 12.6). La      #
# sémantique de statut, alignée sur la conception (REST API Design, 202) :    #
#                                                                             #
# * ``200`` si l'analyse est matérialisée (``READY``) ;                       #
# * ``202`` si elle n'est pas encore matérialisée (absente ou ``PENDING``) :  #
#   la tâche Celery ``analyze_reform`` est déclenchée/entérinée **sans         #
#   bloquer** (Exigences 5.6, 7.11, 12.1, 23.3) et le corps porte le marqueur  #
#   d'indisponibilité en attendant ;                                          #
# * ``200`` avec marqueur d'indisponibilité si une ligne existe marquée        #
#   ``INDISPONIBLE`` — retrieval vide/en échec, aucune régénération relancée   #
#   (Exigences 3.5, 6.5, 7.10, 7.12) ;                                        #
# * ``404`` si la ressource (Problème_Juridique ou Réforme_Proposée) est       #
#   absente (Exigences 1.6, 14.3), sans divulguer de détail interne.          #
# --------------------------------------------------------------------------- #


@router.get(
    "/{problem_id}/analysis",
    response_model=AnalysisResult | Unavailable,
    summary="Analyse_Juridique matérialisée du droit actuel (200/202)",
    responses={
        status.HTTP_200_OK: {"description": "Analyse matérialisée (READY) ou indisponible"},
        status.HTTP_202_ACCEPTED: {"description": "Analyse en préparation (tâche déclenchée)"},
        status.HTTP_404_NOT_FOUND: {"description": "Problème_Juridique introuvable"},
    },
)
async def get_legal_problem_analysis(
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    dispatch: Annotated[AnalysisTaskDispatcher, Depends(get_analysis_task_dispatcher)],
    response: Response,
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
) -> AnalysisResult | Unavailable:
    """Restitue l'Analyse_Juridique matérialisée d'un Problème_Juridique (Exigences 3.5, 7.11).

    Consultation **publique**. ``404`` si le Problème_Juridique n'existe pas
    (Exigences 1.6, 14.3). L'analyse est **matérialisée** par le Worker_Celery :
    ``200`` si ``READY``, ``202`` si pas encore matérialisée (absente ou
    ``PENDING`` — la tâche ``analyze_reform`` est déclenchée sans bloquer), ``200``
    avec marqueur si une ligne existe marquée ``INDISPONIBLE`` (Exigences 3.5,
    7.10, 7.12).
    """
    try:
        await reform_service.get_problem(problem_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc

    result = await analysis_service.get_legal_analysis(problem_id)
    if isinstance(result, AnalysisResult):
        return result

    await _apply_pending_or_unavailable(
        analysis_service, dispatch, response, problem_id, AgentKind.ANALYSE_JURIDIQUE
    )
    return result


@router.get(
    "/{problem_id}/reforms/{reform_id}/simulation",
    response_model=SimulationResult | Unavailable,
    summary="Simulation_De_Conséquences matérialisée (1/5/10 ans) (200/202)",
    responses={
        status.HTTP_200_OK: {"description": "Simulation matérialisée (READY) ou indisponible"},
        status.HTTP_202_ACCEPTED: {"description": "Simulation en préparation (tâche déclenchée)"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
    },
)
async def get_reform_simulation(
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    dispatch: Annotated[AnalysisTaskDispatcher, Depends(get_analysis_task_dispatcher)],
    response: Response,
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
) -> SimulationResult | Unavailable:
    """Restitue la Simulation_De_Conséquences matérialisée d'une Réforme (Exigences 5.6, 5.7).

    Consultation **publique**. La Réforme doit être rattachée au Problème_Juridique :
    ``404`` sinon (Exigences 10.5, 14.3). ``200`` si la Simulation est ``READY`` ;
    ``202`` si pas encore matérialisée (absente ou ``PENDING`` — la tâche
    ``analyze_reform`` est déclenchée sans bloquer, Exigences 5.6, 12.1) ; ``200``
    avec marqueur si une ligne existe marquée ``INDISPONIBLE`` (Exigences 5.7, 7.12).
    """
    await _ensure_reform_link(reform_service, problem_id, reform_id)

    result = await analysis_service.get_simulation(reform_id)
    if isinstance(result, SimulationResult):
        return result

    await _apply_pending_or_unavailable(
        analysis_service, dispatch, response, reform_id, AgentKind.SIMULATION
    )
    return result


@router.get(
    "/{problem_id}/reforms/{reform_id}/analysis",
    response_model=ReformAnalysisBundle,
    summary="Agents_IA_Contradictoires + Conclusion_Technique matérialisés (200/202)",
    responses={
        status.HTTP_200_OK: {"description": "Bundle matérialisé (agents + conclusion)"},
        status.HTTP_202_ACCEPTED: {"description": "Analyses en préparation (tâche déclenchée)"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
    },
)
async def get_reform_analyses(
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    dispatch: Annotated[AnalysisTaskDispatcher, Depends(get_analysis_task_dispatcher)],
    response: Response,
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
) -> ReformAnalysisBundle:
    """Restitue le bundle d'Agents_IA + Conclusion_Technique d'une Réforme (Exigences 7.10–7.12).

    Consultation **publique**. La Réforme doit être rattachée au Problème_Juridique :
    ``404`` sinon (Exigences 10.5, 14.3). Le bundle est **matérialisé** par le
    Worker_Celery : ``200`` dès qu'au moins une analyse existe (chaque agent portant
    son propre marqueur ``READY``/``INDISPONIBLE`` et la Conclusion_Technique étant
    agrégée à partir des seuls agents disponibles — Exigences 7.10, 7.12) ; ``202``
    si **aucune** analyse n'est encore matérialisée (la tâche ``analyze_reform`` est
    déclenchée sans bloquer — Exigences 7.11, 12.1, 23.3).
    """
    await _ensure_reform_link(reform_service, problem_id, reform_id)

    bundle = await analysis_service.get_reform_analyses(reform_id)
    if not await analysis_service.has_materialized_analyses(reform_id):
        # Aucune ligne matérialisée : analyses pas encore préparées ⇒ 202 +
        # déclenchement de la tâche Celery, sans bloquer (Exigences 7.11, 12.1).
        dispatch(reform_id)
        response.status_code = status.HTTP_202_ACCEPTED
    return bundle


async def _ensure_reform_link(
    reform_service: LegalReformService, problem_id: int, reform_id: int
) -> None:
    """Vérifie que la Réforme est rattachée au Problème_Juridique ou lève ``404``.

    Réutilise le contrôle d'existence du :class:`LegalReformService` (le lien
    Réforme ↔ Problème) : la :class:`LegalProblemNotFoundError` levée par le Service
    est convertie en ``404`` normalisé, sans divulguer de détail interne (Exigences
    10.5, 14.2, 14.3).
    """
    try:
        await reform_service.reform_vote_counts(problem_id, reform_id)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc


async def _apply_pending_or_unavailable(
    analysis_service: LegalAnalysisService,
    dispatch: AnalysisTaskDispatcher,
    response: Response,
    reform_id: int,
    kind: AgentKind,
) -> None:
    """Positionne ``202`` (préparation) ou conserve ``200`` (indisponibilité marquée).

    Distingue les deux états restitués par le Service sous forme d':class:`Unavailable`
    (Exigences 3.5, 5.6, 5.7, 6.5, 7.10, 7.12) en sondant le ``status`` matérialisé :

    * ligne marquée ``INDISPONIBLE`` ⇒ retrieval vide/en échec, aucune régénération
      relancée : ``200`` avec le marqueur d'indisponibilité (le code par défaut du
      point d'accès) ;
    * absence de ligne ou ``PENDING`` ⇒ analyse pas encore matérialisée : la tâche
      Celery ``analyze_reform`` est déclenchée sans bloquer et la réponse passe à
      ``202`` (Exigences 5.6, 7.11, 12.1, 23.3).
    """
    materialized_status = await analysis_service.analysis_status(reform_id, kind)
    if materialized_status == _STATUS_INDISPONIBLE:
        return
    dispatch(reform_id)
    response.status_code = status.HTTP_202_ACCEPTED


# --------------------------------------------------------------------------- #
# Points d'accès authentifiés d'écriture (vote, position, amendement,          #
# signalement, explication)                                                    #
#                                                                             #
# Réservés aux Utilisateurs authentifiés via                                  #
# :func:`app.api.deps.get_current_user` (comme ``votes.py`` / ``arguments.py``) :#
# toute tentative anonyme reçoit ``401`` **sans modifier l'état** de la        #
# Plateforme, y compris un Vote_Citoyen existant (Exigences 8.10, 14.4). Le    #
# corps invalide est rejeté en ``422`` par les schémas de requête             #
# (:class:`VoteInput` — ``value ∈ {-1,0,+1}`` ; :class:`PositionInput` —       #
# ``position ∈ {FOR, AGAINST}`` ; :class:`AmendmentInput` /                   #
# :class:`SideEffectInput` — longueur ``1..5000`` ; :class:`ExplainInput`), la #
# réponse énumérant chaque champ en erreur et l'état restant inchangé          #
# (Exigences 8.9, 13.4, 14.5). Une ressource absente (Problème_Juridique ou    #
# Réforme_Proposée non rattachée) est mappée sur ``404`` via                   #
# :func:`_ensure_reform_link` (Exigences 1.6, 14.2, 14.3). Le modèle d'erreur  #
# JSON normalisé et la limitation de débit (``default_rate_limit``) sont ceux  #
# réutilisés par les autres routeurs (renforcée sur ``explain``).             #
# --------------------------------------------------------------------------- #


@router.post(
    "/{problem_id}/reforms/{reform_id}/votes",
    response_model=VoteCounts,
    status_code=status.HTTP_200_OK,
    summary="Voter sur une Réforme_Proposée (UPSERT +1/0/-1)",
    # Limitation de débit du point d'accès sensible de vote (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("legal-votes"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Corps invalide"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def cast_reform_vote(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
    data: VoteInput,
) -> VoteCounts:
    """Enregistre ou met à jour (UPSERT) le Vote_Citoyen sur une Réforme (Exigences 8.1–8.4).

    Réservé aux Utilisateurs authentifiés (``401`` sinon, sans altérer un
    Vote_Citoyen existant — Exigences 8.10, 14.4). ``value`` est borné à
    ``{-1, 0, +1}`` par :class:`VoteInput` (``422`` sinon — Exigence 14.5). La
    Réforme doit être rattachée au Problème_Juridique : ``404`` sinon (Exigences
    9.5, 14.3). Délègue à :meth:`LegalReformService.cast_vote` et renvoie les
    décomptes à jour (``participation_count = support_count + oppose_count``).
    """
    try:
        return await service.cast_vote(current_user, problem_id, reform_id, data.value)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc


@router.post(
    "/{problem_id}/reforms/{reform_id}/positions",
    response_model=ArgumentPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Prendre position FOR/AGAINST sur une Réforme_Proposée",
    # Limitation de débit du point d'accès sensible de contribution (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("legal-positions"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Corps invalide"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def submit_reform_position(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
    data: PositionInput,
) -> ArgumentPublic:
    """Enregistre une prise de position ``FOR``/``AGAINST`` sur une Réforme (Exigences 13.3, 13.4).

    Réservé aux Utilisateurs authentifiés (``401`` sinon — Exigences 8.10, 14.4).
    ``position`` est borné à ``{FOR, AGAINST}`` par :class:`PositionInput` (``422``
    sinon — Exigence 13.4) ; une :class:`InvalidArgumentPositionError` éventuelle
    du Service est également mappée sur ``422``. La Réforme doit être rattachée au
    Problème_Juridique : ``404`` sinon (Exigence 14.3). Délègue à
    :meth:`LegalReformService.submit_position` et renvoie l'Argument créé.
    """
    try:
        argument = await service.submit_position(
            current_user, problem_id, reform_id, data.position, data.content
        )
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc
    except InvalidArgumentPositionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=_INVALID_POSITION_MESSAGE,
        ) from exc
    return ArgumentPublic.model_validate(argument)


@router.post(
    "/{problem_id}/reforms/{reform_id}/amendments",
    response_model=ProposalVersionInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Amender une Réforme_Proposée (→ nouvelle ProposalVersion)",
    # Limitation de débit du point d'accès sensible de contribution (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("legal-amendments"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Corps invalide"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def propose_reform_amendment(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
    data: AmendmentInput,
) -> ProposalVersionInfo:
    """Enregistre un Amendement sur une Réforme_Proposée (Exigences 8.7, 10.1–10.3).

    Réservé aux Utilisateurs authentifiés (``401`` sinon — Exigences 8.10, 14.4).
    ``content`` est borné à ``1..5000`` caractères par :class:`AmendmentInput`
    (``422`` sinon — Exigence 8.9) ; une :class:`ContributionLengthError` éventuelle
    du Service est mappée sur ``422``. La Réforme doit être rattachée au
    Problème_Juridique : ``404`` sinon (Exigence 14.3). Délègue à
    :meth:`LegalReformService.propose_amendment`, qui crée une nouvelle
    :class:`ProposalVersion` append-only, et renvoie l'entrée d'historique créée.
    """
    try:
        version = await service.propose_amendment(current_user, problem_id, reform_id, data)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc
    except (ContributionLengthError, ReformCardinalityError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return ProposalVersionInfo.model_validate(version)


@router.post(
    "/{problem_id}/reforms/{reform_id}/side-effects",
    response_model=SideEffectReportInfo,
    status_code=status.HTTP_201_CREATED,
    summary="Signaler un effet secondaire d'une Réforme_Proposée (→ modération)",
    # Limitation de débit du point d'accès sensible de contribution (Exigence 26.4).
    dependencies=[Depends(default_rate_limit("legal-side-effects"))],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Corps invalide"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def report_reform_side_effect(
    service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
    data: SideEffectInput,
) -> SideEffectReportInfo:
    """Crée un Signalement_D_Effet_Secondaire modéré sur une Réforme (Exigences 8.8, 8.9, 15.1).

    Réservé aux Utilisateurs authentifiés (``401`` sinon — Exigences 8.10, 14.4).
    ``content`` est borné à ``1..5000`` caractères par :class:`SideEffectInput`
    (``422`` sinon — Exigence 8.9) ; une :class:`ContributionLengthError` éventuelle
    du Service est mappée sur ``422``. La Réforme doit être rattachée au
    Problème_Juridique : ``404`` sinon (Exigence 14.3). Délègue à
    :meth:`LegalReformService.report_side_effect`, qui soumet le contenu à la
    modération (``VISIBLE`` / ``PENDING``), et renvoie le Signalement créé.
    """
    try:
        report = await service.report_side_effect(current_user, problem_id, reform_id, data.content)
    except LegalProblemNotFoundError as exc:
        raise _not_found() from exc
    except ContributionLengthError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return SideEffectReportInfo.model_validate(report)


@router.post(
    "/{problem_id}/reforms/{reform_id}/explain",
    response_model=ChatResponse,
    status_code=status.HTTP_200_OK,
    summary="Demander une explication documentée (RAG cité) sur une Réforme",
    # Limitation de débit renforcée du point d'accès RAG (Exigences 26.4, 26.5).
    dependencies=[Depends(_legal_explain_rate_limit)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {"description": "Authentification requise"},
        status.HTTP_404_NOT_FOUND: {
            "description": "Problème_Juridique ou Réforme_Proposée introuvable"
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {"description": "Corps invalide"},
        status.HTTP_429_TOO_MANY_REQUESTS: {"description": "Trop de requêtes"},
    },
)
async def explain_reform(
    reform_service: Annotated[LegalReformService, Depends(get_legal_reform_service)],
    analysis_service: Annotated[LegalAnalysisService, Depends(get_legal_analysis_service)],
    current_user: Annotated[UserPublic, Depends(get_current_user)],
    problem_id: Annotated[int, Path(ge=1, description="Identifiant du Problème_Juridique")],
    reform_id: Annotated[int, Path(ge=1, description="Identifiant de la Réforme_Proposée")],
    data: ExplainInput,
) -> ChatResponse:
    """Répond à une demande d'explication documentée sur une Réforme (Exigences 8.5, 8.6).

    Réservé aux Utilisateurs authentifiés (``401`` sinon — Exigences 8.10, 14.4).
    ``question`` est validé par :class:`ExplainInput` (``422`` sinon). La Réforme
    doit être rattachée au Problème_Juridique : ``404`` sinon (Exigence 14.3).
    Délègue à :meth:`LegalAnalysisService.explain` — récupération obligatoire avant
    génération, en **lecture seule côté domaine** (n'altère jamais un Vote_Citoyen
    existant — Exigences 8.6, 11.3) : la réponse cite au moins une Source, ou porte
    un message d'absence si aucune Source n'est associée.
    """
    await _ensure_reform_link(reform_service, problem_id, reform_id)
    return await analysis_service.explain(current_user, problem_id, reform_id, data.question)
