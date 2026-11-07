"""Schémas Pydantic v2 des Consultations (Exigences 9 et 10).

* :class:`QuestionIn` — question : intitulé 5..300, 2..10 options de 1..200
  caractères pour les choix, facultative par défaut (Exigences 9.2, 9.3, 9.4) ;
* :class:`ConsultationCreate` — consultation de 1 à 20 questions (Exigence 9.1) ;
* :class:`AnswerIn` — réponse à une question ;
* :class:`ConsultationPublic` — consultation telle que vue par un citoyen ;
* :class:`ConsultationResults` — résultats agrégés (ou retenus) ;
* :class:`CsvRow` — ligne de l'export CSV ``question,option_ou_valeur,decompte``.

Les noms de champs sont alignés sur ``app/models/consultation.py``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

QuestionType = Literal["single_choice", "multiple_choice", "scale", "text"]
ConsultationStatus = Literal["draft", "published", "closed"]
EffectiveStatus = Literal["draft", "scheduled", "open", "closed"]

CHOICE_TYPES = ("single_choice", "multiple_choice")
MAX_QUESTIONS = 20
MIN_OPTIONS = 2
MAX_OPTIONS = 10
MAX_TEXT_LENGTH = 2000

REPRESENTATIVITY_NOTICE = "participants volontaires, résultats non représentatifs"


class QuestionIn(BaseModel):
    """Question d'une consultation (Exigences 9.2 à 9.4)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    prompt: str = Field(min_length=5, max_length=300)
    question_type: QuestionType
    required: bool = False
    options: list[str] | None = None

    @field_validator("options")
    @classmethod
    def _check_option_lengths(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        cleaned = [option.strip() for option in value]
        if any(not 1 <= len(option) <= 200 for option in cleaned):
            raise ValueError("chaque option doit comporter de 1 à 200 caractères")
        if len({option.casefold() for option in cleaned}) != len(cleaned):
            raise ValueError("les options doivent être distinctes")
        return cleaned

    @model_validator(mode="after")
    def _check_options_for_type(self) -> QuestionIn:
        if self.question_type in CHOICE_TYPES:
            count = len(self.options or [])
            if not MIN_OPTIONS <= count <= MAX_OPTIONS:
                raise ValueError(
                    f"une question à choix requiert de {MIN_OPTIONS} à {MAX_OPTIONS} options"
                )
        elif self.options:
            raise ValueError("les options ne sont autorisées que pour les questions à choix")
        else:
            self.options = None
        return self


class ConsultationCreate(BaseModel):
    """Corps de création / modification d'une consultation (Exigence 9.1)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    title: str = Field(min_length=5, max_length=120)
    description: str = Field(min_length=20, max_length=5000)
    opens_at: datetime
    closes_at: datetime
    questions: list[QuestionIn] = Field(min_length=1, max_length=MAX_QUESTIONS)

    @model_validator(mode="after")
    def _check_dates(self) -> ConsultationCreate:
        if self.closes_at <= self.opens_at:
            raise ValueError("closes_at doit être postérieure à opens_at")
        return self


class AnswerIn(BaseModel):
    """Réponse d'un citoyen à une question (Exigence 10)."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    question_id: int
    selected_options: list[str] | None = None
    scale_value: int | None = Field(default=None, ge=1, le=5)
    text_value: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)


class QuestionPublic(BaseModel):
    """Question telle que présentée au citoyen."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    position: int
    question_type: QuestionType
    prompt: str
    required: bool
    options: list[str] | None = None


class ConsultationSummary(BaseModel):
    """Entrée de la liste publique des consultations."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    status: EffectiveStatus
    opens_at: datetime
    closes_at: datetime


class ConsultationPublic(ConsultationSummary):
    """Consultation détaillée avec ses questions ordonnées."""

    description: str
    questions: list[QuestionPublic] = Field(default_factory=list)


class OptionCount(BaseModel):
    """Décompte d'une option (choix) ou d'une valeur d'échelle."""

    option: str
    count: int = Field(ge=0)


class QuestionResult(BaseModel):
    """Résultat agrégé d'une question ; ``withheld`` si < seuil de réponses."""

    question_id: int
    position: int
    prompt: str
    question_type: QuestionType
    answer_count: int = Field(ge=0)
    withheld: bool = False
    counts: list[OptionCount] | None = None
    mean: float | None = None
    texts: list[str] | None = None


class ConsultationResults(BaseModel):
    """Résultats d'une consultation clôturée (Exigence 10)."""

    consultation_id: int
    respondent_count: int = Field(ge=0)
    withheld: bool = False
    notice: str = REPRESENTATIVITY_NOTICE
    questions: list[QuestionResult] = Field(default_factory=list)


class CsvRow(BaseModel):
    """Ligne de l'export CSV : ``question,option_ou_valeur,decompte``."""

    question: str
    option_ou_valeur: str
    decompte: int = Field(ge=0)
