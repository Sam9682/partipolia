"""Economic datasets, fiscal parameters and model versions.

A ``ModelVersion`` groups validated datasets. Once ``published`` it is immutable:
its datasets and parameters can no longer change, which keeps every historical
simulation reproducible.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base

DATASET_STATUSES = ("draft", "validated", "rejected")
MODEL_STATUSES = ("draft", "published")
CONFIDENCE_LEVELS = ("high", "medium", "low", "provisional")

model_version_datasets = Table(
    "model_version_datasets",
    Base.metadata,
    Column(
        "model_version_id", ForeignKey("model_versions.id", ondelete="CASCADE"), primary_key=True
    ),
    Column("dataset_id", ForeignKey("economic_datasets.id", ondelete="RESTRICT"), primary_key=True),
)


class EconomicDataset(Base):
    __tablename__ = "economic_datasets"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_economic_datasets_name_version"),
        CheckConstraint(
            "validation_status IN ('draft','validated','rejected')",
            name="ck_economic_datasets_status",
        ),
        CheckConstraint("version >= 1", name="ck_economic_datasets_version_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(255), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    publication_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    reference_year: Mapped[int] = mapped_column(Integer, nullable=False)
    methodology: Mapped[str] = mapped_column(Text, nullable=False, default="")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    imported_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validation_status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    validation_errors: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    parameters: Mapped[list[FiscalParameter]] = relationship(
        "FiscalParameter", back_populates="dataset", cascade="all, delete-orphan"
    )


class FiscalParameter(Base):
    __tablename__ = "fiscal_parameters"
    __table_args__ = (
        UniqueConstraint(
            "dataset_id",
            "category_id",
            "parameter_type",
            "parameter_key",
            name="uq_fiscal_parameters_identity",
        ),
        CheckConstraint(
            "confidence_level IN ('high','medium','low','provisional')",
            name="ck_fiscal_parameters_confidence",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(
        ForeignKey("economic_datasets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("consumption_categories.id", ondelete="RESTRICT"), nullable=True
    )
    parameter_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    parameter_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    value_low: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    parameter_value: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)  # central
    value_high: Mapped[Decimal] = mapped_column(Numeric(24, 10), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    confidence_level: Mapped[str] = mapped_column(String(16), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    dataset: Mapped[EconomicDataset] = relationship("EconomicDataset", back_populates="parameters")


class ModelVersion(Base):
    __tablename__ = "model_versions"
    __table_args__ = (
        CheckConstraint("status IN ('draft','published')", name="ck_model_versions_status"),
        CheckConstraint(
            "(status = 'published') = (published_at IS NOT NULL)",
            name="ck_model_versions_published_at",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="draft")
    notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    datasets: Mapped[list[EconomicDataset]] = relationship(
        "EconomicDataset", secondary=model_version_datasets, lazy="selectin"
    )
