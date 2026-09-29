"""Ajoute les tables « Réparer la loi » (additif, sans altérer l'existant).

Revision ID: 0005_repair_the_law_tables
Revises: 0004_seed_founding_members
Create Date: 2024-01-05 00:00:00.000000

Migration **strictement additive** (Exigence 13.1 : aucune altération des tables
existantes, ``Proposal`` inchangé). Elle crée, dans l'ordre des dépendances de
clés étrangères, les quatre nouvelles tables du domaine « Réparer la loi » :

1. ``legal_problems`` — Problème_Juridique (Exigences 1, 2) :
   CHECK ``complexity_level ∈ {FAIBLE, MOYEN, ELEVE}`` (Exigences 2.2, 2.3),
   CHECK ``affected_citizens_count >= 0``, ``slug`` UNIQUE (Exigence 2.6),
   ``theme_id`` NOT NULL / ``ondelete=RESTRICT`` (Exigence 2.4).
2. ``legal_problem_reforms`` — association Réforme↔Problème (Exigences 2.5, 4.1,
   4.2, 13.1) : ``UNIQUE(problem_id, proposal_id)`` et index partiel
   ``UNIQUE(problem_id) WHERE is_status_quo`` (au plus un statu quo par Problème) ;
   ``problem_id`` ``ondelete=CASCADE``, ``proposal_id`` ``ondelete=RESTRICT``.
3. ``side_effect_reports`` — Signalement_D_Effet_Secondaire (Exigences 8.8, 8.9) :
   CHECK ``status ∈ {PENDING, VISIBLE, REJECTED}`` et ``char_length(content) <= 5000``.
4. ``legal_analyses`` — matérialisation des analyses IA (Exigences 3, 5, 6, 7,
   12.6) : ``UNIQUE(proposal_id, kind)``, CHECK ``kind`` (9 agents), CHECK
   ``status ∈ {PENDING, READY, INDISPONIBLE}``, CHECK ``confidence ∈ [0, 1]``.

Le ``downgrade`` supprime ces tables dans l'ordre inverse, restituant l'état
antérieur. Reproductibilité complète ``alembic upgrade head`` / ``downgrade``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Identifiants de révision, utilisés par Alembic.
revision: str = "0005_repair_the_law_tables"
down_revision: str | None = "0004_seed_founding_members"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Crée les quatre tables « Réparer la loi » (migration additive)."""

    # ------------------------------------------------------------------ #
    # 1. legal_problems (Exigences 1, 2) — dépend de themes              #
    # ------------------------------------------------------------------ #
    op.create_table(
        "legal_problems",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column("affected_citizens_count", sa.Integer(), nullable=False),
        sa.Column("concerned_legal_texts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("jurisprudence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("complexity_level", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "complexity_level IN ('FAIBLE', 'MOYEN', 'ELEVE')",
            name="ck_legal_problems_complexity_level",
        ),
        sa.CheckConstraint(
            "affected_citizens_count >= 0",
            name="ck_legal_problems_affected_nonneg",
        ),
        sa.ForeignKeyConstraint(
            ["theme_id"], ["themes.id"],
            name="fk_legal_problems_theme_id_themes", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_legal_problems"),
        sa.UniqueConstraint("slug", name="uq_legal_problems_slug"),
    )
    op.create_index("ix_legal_problems_slug", "legal_problems", ["slug"], unique=True)
    op.create_index("ix_legal_problems_theme_id", "legal_problems", ["theme_id"], unique=False)

    # ------------------------------------------------------------------ #
    # 2. legal_problem_reforms (Exigences 2.5, 4.1, 4.2, 13.1)           #
    #    dépend de legal_problems + proposals                            #
    # ------------------------------------------------------------------ #
    op.create_table(
        "legal_problem_reforms",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("problem_id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("is_status_quo", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["problem_id"], ["legal_problems.id"],
            name="fk_legal_problem_reforms_problem_id_legal_problems", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_legal_problem_reforms_proposal_id_proposals", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_legal_problem_reforms"),
        sa.UniqueConstraint("problem_id", "proposal_id", name="uq_lpr_problem_proposal"),
    )
    op.create_index(
        "ix_legal_problem_reforms_problem_id", "legal_problem_reforms", ["problem_id"], unique=False
    )
    op.create_index(
        "ix_legal_problem_reforms_proposal_id", "legal_problem_reforms", ["proposal_id"], unique=False
    )
    # Index partiel UNIQUE(problem_id) WHERE is_status_quo : au plus un statu quo
    # par Problème (défense en profondeur — Exigence 4.2).
    op.create_index(
        "uq_lpr_one_status_quo",
        "legal_problem_reforms",
        ["problem_id"],
        unique=True,
        postgresql_where=sa.text("is_status_quo"),
    )

    # ------------------------------------------------------------------ #
    # 3. side_effect_reports (Exigences 8.8, 8.9)                        #
    #    dépend de proposals + users                                     #
    # ------------------------------------------------------------------ #
    op.create_table(
        "side_effect_reports",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('PENDING', 'VISIBLE', 'REJECTED')",
            name="ck_side_effect_reports_status",
        ),
        sa.CheckConstraint(
            "char_length(content) <= 5000",
            name="ck_side_effect_reports_len",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_side_effect_reports_proposal_id_proposals", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"],
            name="fk_side_effect_reports_author_id_users", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_side_effect_reports"),
    )
    op.create_index(
        "ix_side_effect_reports_proposal_id", "side_effect_reports", ["proposal_id"], unique=False
    )
    op.create_index(
        "ix_side_effect_reports_author_id", "side_effect_reports", ["author_id"], unique=False
    )

    # ------------------------------------------------------------------ #
    # 4. legal_analyses (Exigences 3, 5, 6, 7, 12.6) — dépend de proposals#
    # ------------------------------------------------------------------ #
    op.create_table(
        "legal_analyses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("doc_version", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('ANALYSE_JURIDIQUE','SIMULATION','EFFETS_PERVERS',"
            "'JURISTE','BUDGET','CONSTITUTION','IMPACT','OPPOSANT','DEFENSEUR')",
            name="ck_legal_analyses_kind",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','READY','INDISPONIBLE')",
            name="ck_legal_analyses_status",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_legal_analyses_confidence_range",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_legal_analyses_proposal_id_proposals", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_legal_analyses"),
        sa.UniqueConstraint("proposal_id", "kind", name="uq_legal_analyses_proposal_kind"),
    )
    op.create_index("ix_legal_analyses_proposal_id", "legal_analyses", ["proposal_id"], unique=False)


def downgrade() -> None:
    """Supprime les quatre tables « Réparer la loi » dans l'ordre inverse."""

    op.drop_index("ix_legal_analyses_proposal_id", table_name="legal_analyses")
    op.drop_table("legal_analyses")

    op.drop_index("ix_side_effect_reports_author_id", table_name="side_effect_reports")
    op.drop_index("ix_side_effect_reports_proposal_id", table_name="side_effect_reports")
    op.drop_table("side_effect_reports")

    op.drop_index("uq_lpr_one_status_quo", table_name="legal_problem_reforms")
    op.drop_index("ix_legal_problem_reforms_proposal_id", table_name="legal_problem_reforms")
    op.drop_index("ix_legal_problem_reforms_problem_id", table_name="legal_problem_reforms")
    op.drop_table("legal_problem_reforms")

    op.drop_index("ix_legal_problems_theme_id", table_name="legal_problems")
    op.drop_index("ix_legal_problems_slug", table_name="legal_problems")
    op.drop_table("legal_problems")
