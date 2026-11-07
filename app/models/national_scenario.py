"""Scénarios nationaux enregistrés et liens de partage en lecture seule.

Les ``Decimal`` des paramètres sont sérialisés en chaînes dans ``parameters``
(aucun flottant). Seul le hachage SHA-256 du jeton de partage est stocké.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin


class NationalScenario(TimestampMixin, Base):
    __tablename__ = "national_scenarios"
    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="ck_national_scenarios_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    model_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_versions.id", ondelete="RESTRICT"), nullable=True
    )
    model_version: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[str] = mapped_column(Text, nullable=False)
    net_impact_central: Mapped[Decimal] = mapped_column(Numeric(18, 2), nullable=False)


class ScenarioShareLink(CreatedAtMixin, Base):
    __tablename__ = "scenario_share_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("national_scenarios.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
