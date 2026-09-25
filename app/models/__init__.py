"""Modèles SQLAlchemy 2.x (style ``Mapped`` typé).

Ce module importe et ré-exporte l'ensemble des modèles ORM du paquet afin que
``Base.metadata`` référence chaque table. C'est indispensable pour :

- l'autogénération des migrations Alembic (toutes les tables doivent être
  connues de la métadonnée cible) ;
- la résolution des relations déclarées par chaîne de caractères
  (``relationship("AutreModèle")``), qui exige que les classes cibles soient
  chargées.
"""

from app.models.mixins import CreatedAtMixin, TimestampMixin
from app.models.user import User
from app.models.theme import Theme
from app.models.proposal import PROPOSAL_STATUSES, Proposal, ProposalVersion
from app.models.vote import Vote
from app.models.argument import ARGUMENT_POSITIONS, Argument
from app.models.comment import COMMENT_STATUSES, Comment
from app.models.source import SOURCE_TYPES, ProposalSource, Source
from app.models.document import Document, DocumentChunk
from app.models.program import Program, ProgramProposal
from app.models.team import Team, TeamMember
from app.models.mandate import COMMITMENT_STATUSES, Commitment, Indicator
from app.models.audit import AuditLog

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
    # Audit
    "AuditLog",
]
