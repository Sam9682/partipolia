"""Create consultation tables (anonymous responses, no user link).

Revision ID: 0014_consultations
Revises: 0013_claims
Create Date: 2026-11-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014_consultations"
down_revision: str | None = "0013_claims"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "consultations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("opens_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("closes_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_consultations_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('draft','published','closed')", name="ck_consultations_status"
        ),
        sa.CheckConstraint("closes_at > opens_at", name="ck_consultations_dates"),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 5 AND 120", name="ck_consultations_title_len"
        ),
        sa.CheckConstraint(
            "char_length(description) BETWEEN 20 AND 5000",
            name="ck_consultations_description_len",
        ),
    )
    op.create_index(
        "ix_consultations_status_closes_at", "consultations", ["status", "closes_at"]
    )

    op.create_table(
        "consultation_questions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("consultation_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("question_type", sa.String(length=16), nullable=False),
        sa.Column("prompt", sa.String(length=300), nullable=False),
        sa.Column("required", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.ForeignKeyConstraint(
            ["consultation_id"],
            ["consultations.id"],
            name="fk_consultation_questions_consultation_id_consultations",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "consultation_id", "position", name="uq_consultation_questions_position"
        ),
        sa.CheckConstraint(
            "question_type IN ('single_choice','multiple_choice','scale','text')",
            name="ck_consultation_questions_type",
        ),
        sa.CheckConstraint("position >= 1", name="ck_consultation_questions_position"),
        sa.CheckConstraint(
            "char_length(prompt) BETWEEN 5 AND 300", name="ck_consultation_questions_prompt_len"
        ),
    )

    op.create_table(
        "consultation_participations",
        sa.Column("consultation_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "participated_on", sa.Date(), server_default=sa.func.current_date(), nullable=False
        ),
        sa.PrimaryKeyConstraint(
            "consultation_id", "user_id", name="pk_consultation_participations"
        ),
        sa.ForeignKeyConstraint(
            ["consultation_id"],
            ["consultations.id"],
            name="fk_consultation_participations_consultation_id_consultations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_consultation_participations_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_consultation_participations_user", "consultation_participations", ["user_id"]
    )

    # Aucun user_id, aucun horodatage : réponses anonymes (D6).
    op.create_table(
        "consultation_responses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("consultation_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["consultation_id"],
            ["consultations.id"],
            name="fk_consultation_responses_consultation_id_consultations",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_consultation_responses_consultation", "consultation_responses", ["consultation_id"]
    )

    op.create_table(
        "consultation_answers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("response_id", sa.Uuid(), nullable=False),
        sa.Column("question_id", sa.Integer(), nullable=False),
        sa.Column("selected_options", postgresql.JSONB(), nullable=True),
        sa.Column("scale_value", sa.Integer(), nullable=True),
        sa.Column("text_value", sa.Text(), nullable=True),
        sa.Column("text_review", sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(
            ["response_id"],
            ["consultation_responses.id"],
            name="fk_consultation_answers_response_id_consultation_responses",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["question_id"],
            ["consultation_questions.id"],
            name="fk_consultation_answers_question_id_consultation_questions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "response_id", "question_id", name="uq_consultation_answers_response_question"
        ),
        sa.CheckConstraint(
            "scale_value IS NULL OR scale_value BETWEEN 1 AND 5",
            name="ck_consultation_answers_scale",
        ),
        sa.CheckConstraint(
            "text_value IS NULL OR char_length(text_value) <= 2000",
            name="ck_consultation_answers_text_len",
        ),
        sa.CheckConstraint(
            "text_review IS NULL OR text_review IN ('pending','approved','rejected')",
            name="ck_consultation_answers_text_review",
        ),
    )
    op.create_index("ix_consultation_answers_question", "consultation_answers", ["question_id"])


def downgrade() -> None:
    op.drop_index("ix_consultation_answers_question", table_name="consultation_answers")
    op.drop_table("consultation_answers")
    op.drop_index("ix_consultation_responses_consultation", table_name="consultation_responses")
    op.drop_table("consultation_responses")
    op.drop_index(
        "ix_consultation_participations_user", table_name="consultation_participations"
    )
    op.drop_table("consultation_participations")
    op.drop_table("consultation_questions")
    op.drop_index("ix_consultations_status_closes_at", table_name="consultations")
    op.drop_table("consultations")
