"""Consultations structurées, questions, participations et réponses anonymes.

Anonymat (D6) : ``ConsultationParticipation`` relie un utilisateur à une
consultation (date seule, pour garantir l'unicité de la participation) tandis
que ``ConsultationResponse`` et ``ConsultationAnswer`` ne portent **aucun**
``user_id`` ni horodatage. Aucun lien ne permet donc de rapprocher un citoyen
de ses réponses.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import TimestampMixin

CONSULTATION_STATUSES = ("draft", "published", "closed")
QUESTION_TYPES = ("single_choice", "multiple_choice", "scale", "text")
TEXT_REVIEW_STATUSES = ("pending", "approved", "rejected")


class Consultation(TimestampMixin, Base):
    __tablename__ = "consultations"
    __table_args__ = (
        CheckConstraint("status IN ('draft','published','closed')", name="ck_consultations_status"),
        CheckConstraint("closes_at > opens_at", name="ck_consultations_dates"),
        CheckConstraint("char_length(title) BETWEEN 5 AND 120", name="ck_consultations_title_len"),
        CheckConstraint(
            "char_length(description) BETWEEN 20 AND 5000",
            name="ck_consultations_description_len",
        ),
        Index("ix_consultations_status_closes_at", "status", "closes_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    opens_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    closes_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )


class ConsultationQuestion(Base):
    __tablename__ = "consultation_questions"
    __table_args__ = (
        UniqueConstraint("consultation_id", "position", name="uq_consultation_questions_position"),
        CheckConstraint(
            "question_type IN ('single_choice','multiple_choice','scale','text')",
            name="ck_consultation_questions_type",
        ),
        CheckConstraint("position >= 1", name="ck_consultation_questions_position"),
        CheckConstraint(
            "char_length(prompt) BETWEEN 5 AND 300", name="ck_consultation_questions_prompt_len"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    consultation_id: Mapped[int] = mapped_column(
        ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    question_type: Mapped[str] = mapped_column(String(16), nullable=False)
    prompt: Mapped[str] = mapped_column(String(300), nullable=False)
    required: Mapped[bool] = mapped_column(
        nullable=False, default=True, server_default=text("true")
    )
    options: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)


class ConsultationParticipation(Base):
    __tablename__ = "consultation_participations"
    __table_args__ = (
        PrimaryKeyConstraint("consultation_id", "user_id", name="pk_consultation_participations"),
        Index("ix_consultation_participations_user", "user_id"),
    )

    consultation_id: Mapped[int] = mapped_column(
        ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    # Date seule (pas d'heure) afin de limiter la corrélation avec les réponses.
    participated_on: Mapped[date] = mapped_column(
        Date, nullable=False, server_default=func.current_date()
    )


class ConsultationResponse(Base):
    """Réponse anonyme : ni ``user_id`` ni horodatage, identifiant aléatoire."""

    __tablename__ = "consultation_responses"
    __table_args__ = (Index("ix_consultation_responses_consultation", "consultation_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    consultation_id: Mapped[int] = mapped_column(
        ForeignKey("consultations.id", ondelete="CASCADE"), nullable=False
    )


class ConsultationAnswer(Base):
    __tablename__ = "consultation_answers"
    __table_args__ = (
        UniqueConstraint(
            "response_id", "question_id", name="uq_consultation_answers_response_question"
        ),
        CheckConstraint(
            "scale_value IS NULL OR scale_value BETWEEN 1 AND 5",
            name="ck_consultation_answers_scale",
        ),
        CheckConstraint(
            "text_value IS NULL OR char_length(text_value) <= 2000",
            name="ck_consultation_answers_text_len",
        ),
        CheckConstraint(
            "text_review IS NULL OR text_review IN ('pending','approved','rejected')",
            name="ck_consultation_answers_text_review",
        ),
        Index("ix_consultation_answers_question", "question_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    response_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("consultation_responses.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("consultation_questions.id", ondelete="CASCADE"), nullable=False
    )
    selected_options: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    scale_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_review: Mapped[str | None] = mapped_column(String(16), nullable=True)
