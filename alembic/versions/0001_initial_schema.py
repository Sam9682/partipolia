"""Migration initiale — schéma complet PARTIPOLAI (18 tables) + recherche.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2024-01-01 00:00:00.000000

Cette migration crée l'intégralité du schéma applicatif à partir des modèles
ORM de ``app/models`` (SQLAlchemy 2.x). Elle est écrite à la main afin de
maîtriser l'ordre de création (dépendances de clés étrangères) et d'ajouter les
objets spécifiques à PostgreSQL/pgvector qui ne sont pas produits par
l'``autogenerate`` :

* extension ``vector`` (pgvector) — Exigences 11.6, 32.3 ;
* index **HNSW** ``vector_cosine_ops`` sur ``document_chunks.embedding`` — 11.6 ;
* colonne ``tsvector`` **générée** sur ``document_chunks.content`` (config
  ``french``) + index **GIN** pour la recherche lexicale — Exigence 12.1 ;
* reproductibilité complète ``alembic upgrade head`` / ``downgrade`` — 32.4.

La dimension du vecteur est lue depuis ``settings.embedding_dim`` (défaut 1536),
conformément au modèle ``DocumentChunk`` (Exigence 11.6).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.config import settings

# Identifiants de révision, utilisés par Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Dimension du vecteur d'embedding (configurable — Exigence 11.6).
_EMBEDDING_DIM = settings.embedding_dim


def upgrade() -> None:
    """Crée l'extension pgvector, toutes les tables, puis les objets de recherche."""

    # ------------------------------------------------------------------ #
    # 0. Extension pgvector (Exigences 11.6, 32.3)                       #
    # ------------------------------------------------------------------ #
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # ------------------------------------------------------------------ #
    # 1. Tables sans dépendance (racines du graphe de FK)               #
    # ------------------------------------------------------------------ #

    # users (Exigences 1.1, 1.3)
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("is_admin", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # themes (Exigences 2.1, 2.5)
    op.create_table(
        "themes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_themes"),
        sa.UniqueConstraint("slug", name="uq_themes_slug"),
    )
    op.create_index("ix_themes_slug", "themes", ["slug"], unique=True)

    # programs (Exigence 18.1)
    op.create_table(
        "programs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_programs"),
    )

    # teams (Exigence 20.1)
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_teams"),
    )

    # commitments (Exigences 21.1–21.3)
    op.create_table(
        "commitments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("progress", sa.Float(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'MODIFIED', 'ABANDONED')",
            name="ck_commitments_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_commitments"),
    )

    # audit_logs (Exigences 29.1, 29.2)
    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=120), nullable=False),
        sa.Column("entity_type", sa.String(length=120), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("old_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("ip_hash", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"], unique=False)
    op.create_index("ix_audit_logs_entity_type", "audit_logs", ["entity_type"], unique=False)
    op.create_index("ix_audit_logs_entity_id", "audit_logs", ["entity_id"], unique=False)

    # sources (Exigences 10.1, 10.2)
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=True),
        sa.Column("publisher", sa.String(length=255), nullable=True),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("publication_date", sa.Date(), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('OFFICIAL', 'ACADEMIC', 'STATISTICAL', 'MEDIA', "
            "'REPORT', 'LEGISLATION', 'OTHER')",
            name="ck_sources_source_type",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sources"),
    )

    # ------------------------------------------------------------------ #
    # 2. Tables dépendant des racines                                    #
    # ------------------------------------------------------------------ #

    # proposals (Exigences 3.1–3.5) — dépend de themes + users
    op.create_table(
        "proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("theme_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=255), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("expected_impact", sa.Text(), nullable=True),
        sa.Column("implementation_delay", sa.String(length=120), nullable=True),
        sa.Column("estimated_cost", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("estimated_savings", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("estimated_revenue", sa.Numeric(precision=18, scale=2), nullable=True),
        sa.Column("funding_description", sa.Text(), nullable=True),
        sa.Column("legal_constraints", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING_REVIEW', 'PUBLISHED', 'ARCHIVED', 'REJECTED')",
            name="ck_proposals_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_proposals_version_positive"),
        sa.ForeignKeyConstraint(
            ["theme_id"], ["themes.id"], name="fk_proposals_theme_id_themes", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_proposals_author_id_users", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposals"),
        sa.UniqueConstraint("slug", name="uq_proposals_slug"),
    )
    op.create_index("ix_proposals_theme_id", "proposals", ["theme_id"], unique=False)
    op.create_index("ix_proposals_author_id", "proposals", ["author_id"], unique=False)
    op.create_index("ix_proposals_slug", "proposals", ["slug"], unique=True)

    # proposal_versions (Exigences 3.4, 3.5) — dépend de proposals + users
    op.create_table(
        "proposal_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("edited_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("version >= 1", name="ck_proposal_versions_version_positive"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_proposal_versions_proposal_id_proposals", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["edited_by"], ["users.id"],
            name="fk_proposal_versions_edited_by_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal_versions"),
    )
    op.create_index("ix_proposal_versions_proposal_id", "proposal_versions", ["proposal_id"], unique=False)
    op.create_index("ix_proposal_versions_edited_by", "proposal_versions", ["edited_by"], unique=False)

    # votes (Exigences 6.2, 8.1) — dépend de proposals + users
    op.create_table(
        "votes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("value IN (-1, 0, 1)", name="ck_votes_value"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"], name="fk_votes_proposal_id_proposals", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_votes_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_votes"),
        sa.UniqueConstraint("proposal_id", "user_id", name="uq_votes_proposal_user"),
    )
    op.create_index("ix_votes_proposal_id", "votes", ["proposal_id"], unique=False)
    op.create_index("ix_votes_user_id", "votes", ["user_id"], unique=False)

    # arguments (Exigence 8.1) — dépend de proposals + users
    op.create_table(
        "arguments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("position IN ('FOR', 'AGAINST')", name="ck_arguments_position"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"], name="fk_arguments_proposal_id_proposals", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_arguments_author_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_arguments"),
    )
    op.create_index("ix_arguments_proposal_id", "arguments", ["proposal_id"], unique=False)
    op.create_index("ix_arguments_author_id", "arguments", ["author_id"], unique=False)

    # comments (Exigences 9.2, 9.4) — auto-référence via parent_id
    op.create_table(
        "comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status IN ('VISIBLE', 'HIDDEN', 'PENDING')", name="ck_comments_status"),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"], name="fk_comments_proposal_id_proposals", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["author_id"], ["users.id"], name="fk_comments_author_id_users", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["comments.id"], name="fk_comments_parent_id_comments", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_comments"),
    )
    op.create_index("ix_comments_proposal_id", "comments", ["proposal_id"], unique=False)
    op.create_index("ix_comments_author_id", "comments", ["author_id"], unique=False)
    op.create_index("ix_comments_parent_id", "comments", ["parent_id"], unique=False)

    # proposal_sources (Exigence 10.3) — dépend de proposals + sources
    op.create_table(
        "proposal_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("relevance_score", sa.Float(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "relevance_score >= 0 AND relevance_score <= 1",
            name="ck_proposal_sources_relevance_score_range",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_proposal_sources_proposal_id_proposals", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"],
            name="fk_proposal_sources_source_id_sources", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_proposal_sources"),
    )
    op.create_index("ix_proposal_sources_proposal_id", "proposal_sources", ["proposal_id"], unique=False)
    op.create_index("ix_proposal_sources_source_id", "proposal_sources", ["source_id"], unique=False)

    # documents (Exigence 11.3) — dépend de sources
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("checksum", sa.String(length=128), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_documents_source_id_sources", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_documents"),
        sa.UniqueConstraint("checksum", name="uq_documents_checksum"),
    )
    op.create_index("ix_documents_source_id", "documents", ["source_id"], unique=False)
    op.create_index("ix_documents_checksum", "documents", ["checksum"], unique=True)

    # document_chunks (Exigences 11.5, 11.6, 12.1) — dépend de documents
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("embedding", Vector(_EMBEDDING_DIM), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"],
            name="fk_document_chunks_document_id_documents", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_document_chunks"),
    )
    op.create_index("ix_document_chunks_document_id", "document_chunks", ["document_id"], unique=False)

    # program_proposals (Exigences 18.2, 18.4) — dépend de programs + proposals
    op.create_table(
        "program_proposals",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("program_id", sa.Integer(), nullable=False),
        sa.Column("proposal_id", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("included_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["program_id"], ["programs.id"],
            name="fk_program_proposals_program_id_programs", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["proposal_id"], ["proposals.id"],
            name="fk_program_proposals_proposal_id_proposals", ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_program_proposals"),
    )
    op.create_index("ix_program_proposals_program_id", "program_proposals", ["program_id"], unique=False)
    op.create_index("ix_program_proposals_proposal_id", "program_proposals", ["proposal_id"], unique=False)

    # team_members (Exigence 20.2) — dépend de teams + users
    op.create_table(
        "team_members",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column("bio", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"], name="fk_team_members_team_id_teams", ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_team_members_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_team_members"),
    )
    op.create_index("ix_team_members_team_id", "team_members", ["team_id"], unique=False)
    op.create_index("ix_team_members_user_id", "team_members", ["user_id"], unique=False)

    # indicators (Exigences 21.1, 21.4) — dépend de sources
    op.create_table(
        "indicators",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("baseline", sa.Float(), nullable=True),
        sa.Column("target", sa.Float(), nullable=True),
        sa.Column("current_value", sa.Float(), nullable=True),
        sa.Column("source_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id"], ["sources.id"], name="fk_indicators_source_id_sources", ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_indicators"),
    )
    op.create_index("ix_indicators_source_id", "indicators", ["source_id"], unique=False)

    # ------------------------------------------------------------------ #
    # 3. Recherche vectorielle & lexicale sur document_chunks           #
    # ------------------------------------------------------------------ #

    # Index HNSW pour la recherche sémantique par distance cosinus (Exigence 11.6).
    op.execute(
        "CREATE INDEX ix_document_chunks_embedding_hnsw "
        "ON document_chunks USING hnsw (embedding vector_cosine_ops)"
    )

    # Colonne tsvector GÉNÉRÉE sur content (config 'french') + index GIN pour la
    # recherche lexicale (Exigence 12.1). La colonne est calculée par la base ;
    # elle reste cohérente à chaque écriture du contenu.
    op.execute(
        "ALTER TABLE document_chunks "
        "ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('french', content)) STORED"
    )
    op.execute(
        "CREATE INDEX ix_document_chunks_content_tsv "
        "ON document_chunks USING gin (content_tsv)"
    )


def downgrade() -> None:
    """Supprime tous les objets créés par ``upgrade`` dans l'ordre inverse."""

    # ------------------------------------------------------------------ #
    # 3'. Objets de recherche sur document_chunks (ordre inverse)       #
    # ------------------------------------------------------------------ #
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_content_tsv")
    op.execute("ALTER TABLE document_chunks DROP COLUMN IF EXISTS content_tsv")
    op.execute("DROP INDEX IF EXISTS ix_document_chunks_embedding_hnsw")

    # ------------------------------------------------------------------ #
    # 2'. Tables dépendantes (ordre inverse de création)                #
    # ------------------------------------------------------------------ #
    op.drop_index("ix_indicators_source_id", table_name="indicators")
    op.drop_table("indicators")

    op.drop_index("ix_team_members_user_id", table_name="team_members")
    op.drop_index("ix_team_members_team_id", table_name="team_members")
    op.drop_table("team_members")

    op.drop_index("ix_program_proposals_proposal_id", table_name="program_proposals")
    op.drop_index("ix_program_proposals_program_id", table_name="program_proposals")
    op.drop_table("program_proposals")

    op.drop_index("ix_document_chunks_document_id", table_name="document_chunks")
    op.drop_table("document_chunks")

    op.drop_index("ix_documents_checksum", table_name="documents")
    op.drop_index("ix_documents_source_id", table_name="documents")
    op.drop_table("documents")

    op.drop_index("ix_proposal_sources_source_id", table_name="proposal_sources")
    op.drop_index("ix_proposal_sources_proposal_id", table_name="proposal_sources")
    op.drop_table("proposal_sources")

    op.drop_index("ix_comments_parent_id", table_name="comments")
    op.drop_index("ix_comments_author_id", table_name="comments")
    op.drop_index("ix_comments_proposal_id", table_name="comments")
    op.drop_table("comments")

    op.drop_index("ix_arguments_author_id", table_name="arguments")
    op.drop_index("ix_arguments_proposal_id", table_name="arguments")
    op.drop_table("arguments")

    op.drop_index("ix_votes_user_id", table_name="votes")
    op.drop_index("ix_votes_proposal_id", table_name="votes")
    op.drop_table("votes")

    op.drop_index("ix_proposal_versions_edited_by", table_name="proposal_versions")
    op.drop_index("ix_proposal_versions_proposal_id", table_name="proposal_versions")
    op.drop_table("proposal_versions")

    op.drop_index("ix_proposals_slug", table_name="proposals")
    op.drop_index("ix_proposals_author_id", table_name="proposals")
    op.drop_index("ix_proposals_theme_id", table_name="proposals")
    op.drop_table("proposals")

    # ------------------------------------------------------------------ #
    # 1'. Tables racines (ordre inverse de création)                    #
    # ------------------------------------------------------------------ #
    op.drop_table("sources")

    op.drop_index("ix_audit_logs_entity_id", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity_type", table_name="audit_logs")
    op.drop_index("ix_audit_logs_action", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_table("commitments")
    op.drop_table("teams")
    op.drop_table("programs")

    op.drop_index("ix_themes_slug", table_name="themes")
    op.drop_table("themes")

    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")

    # Note : l'extension ``vector`` n'est PAS supprimée au downgrade — elle peut
    # être partagée par d'autres objets et sa suppression est une opération à
    # l'échelle de la base, hors du périmètre de cette migration.
