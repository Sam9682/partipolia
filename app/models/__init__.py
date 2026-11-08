"""Modèles SQLAlchemy 2.x (style ``Mapped`` typé).

Ce module importe et ré-exporte l'ensemble des modèles ORM du paquet afin que
``Base.metadata`` référence chaque table. C'est indispensable pour :

- l'autogénération des migrations Alembic (toutes les tables doivent être
  connues de la métadonnée cible) ;
- la résolution des relations déclarées par chaîne de caractères
  (``relationship("AutreModèle")``), qui exige que les classes cibles soient
  chargées.
"""

from app.models.aggregation import AggregatedImpact
from app.models.argument import ARGUMENT_POSITIONS, Argument
from app.models.audit import AuditLog
from app.models.citizen_commitment import CitizenCommitment
from app.models.claim import (
    CLAIM_COMMENT_STATUSES,
    CLAIM_STATUSES,
    REPORT_REASONS,
    Claim,
    ClaimComment,
    ClaimSupport,
    ContentReport,
)
from app.models.comment import COMMENT_STATUSES, Comment
from app.models.consultation import (
    CONSULTATION_STATUSES,
    QUESTION_TYPES,
    TEXT_REVIEW_STATUSES,
    Consultation,
    ConsultationAnswer,
    ConsultationParticipation,
    ConsultationQuestion,
    ConsultationResponse,
)
from app.models.consumption_category import ConsumptionCategory
from app.models.document import Document, DocumentChunk
from app.models.economic_dataset import (
    EconomicDataset,
    FiscalParameter,
    ModelVersion,
    model_version_datasets,
)
from app.models.legal_analysis import LegalAnalysis
from app.models.legal_problem import LegalProblem
from app.models.legal_problem_reform import LegalProblemReform
from app.models.mandate import COMMITMENT_STATUSES, Commitment, Indicator
from app.models.mixins import CreatedAtMixin, TimestampMixin
from app.models.national_scenario import NationalScenario, ScenarioShareLink
from app.models.net_balance_verdict import VERDICT_VALUES, NetBalanceVerdict
from app.models.program import Program, ProgramProposal
from app.models.proposal import PROPOSAL_STATUSES, Proposal, ProposalVersion
from app.models.side_effect_report import (
    SIDE_EFFECT_REPORT_STATUSES,
    SideEffectReport,
)
from app.models.simulation import EconomicSimulation, SimulationResult
from app.models.source import SOURCE_TYPES, ProposalSource, Source
from app.models.team import Team, TeamMember
from app.models.theme import Theme
from app.models.user import User
from app.models.vote import Vote

__all__ = [
    # Mixins
    "CreatedAtMixin",
    "TimestampMixin",
    # Utilisateurs / thèmes
    "User",
    "Theme",
    # Propositions
    "Proposal",
    "ProposalVersion",
    "PROPOSAL_STATUSES",
    # Votes
    "Vote",
    # Arguments
    "Argument",
    "ARGUMENT_POSITIONS",
    # Commentaires
    "Comment",
    "COMMENT_STATUSES",
    # Sources
    "Source",
    "ProposalSource",
    "SOURCE_TYPES",
    # Documents
    "Document",
    "DocumentChunk",
    # Programmes
    "Program",
    "ProgramProposal",
    # Équipes
    "Team",
    "TeamMember",
    # Mandat / suivi
    "Commitment",
    "Indicator",
    "COMMITMENT_STATUSES",
    # Partipolia — économie citoyenne
    "ConsumptionCategory",
    "EconomicDataset",
    "FiscalParameter",
    "ModelVersion",
    "model_version_datasets",
    "EconomicSimulation",
    "SimulationResult",
    "AggregatedImpact",
    "CitizenCommitment",
    "NationalScenario",
    "ScenarioShareLink",
    "NetBalanceVerdict",
    "VERDICT_VALUES",
    # Revendications citoyennes
    "Claim",
    "ClaimSupport",
    "ClaimComment",
    "ContentReport",
    "CLAIM_STATUSES",
    "CLAIM_COMMENT_STATUSES",
    "REPORT_REASONS",
    # Consultations
    "Consultation",
    "ConsultationQuestion",
    "ConsultationParticipation",
    "ConsultationResponse",
    "ConsultationAnswer",
    "CONSULTATION_STATUSES",
    "QUESTION_TYPES",
    "TEXT_REVIEW_STATUSES",
    # Audit
    "AuditLog",
    # Réparer la loi
    "LegalProblem",
    "LegalProblemReform",
    "LegalAnalysis",
    "SideEffectReport",
    "SIDE_EFFECT_REPORT_STATUSES",
]
