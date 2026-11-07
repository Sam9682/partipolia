"""Revendications citoyennes, soutiens, commentaires et signalements.

Ces modèles sont indépendants des modèles hérités (``Proposal``, ``Comment``,
``Theme``…) : tables distinctes, aucune clé étrangère vers elles.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.mixins import CreatedAtMixin, TimestampMixin

CLAIM_STATUSES = ("draft", "published", "hidden", "closed")
CLAIM_COMMENT_STATUSES = ("visible", "hidden")
REPORT_REASONS = ("illegal", "hate_harassment", "off_topic", "duplicate", "other")


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','published','hidden','closed')", name="ck_claims_status"
        ),
        CheckConstraint("support_count >= 0", name="ck_claims_support_count"),
        CheckConstraint("report_count >= 0", name="ck_claims_report_count"),
        CheckConstraint("char_length(title) BETWEEN 5 AND 120", name="ck_claims_title_len"),
        CheckConstraint("char_length(body) BETWEEN 20 AND 2000", name="ck_claims_body_len"),
        UniqueConstraint("author_id", "fingerprint", name="uq_claims_author_fingerprint"),
        Index(
            "ix_claims_published_date",
            "published_at",
            "id",
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_claims_published_support",
            "support_count",
            "id",
            postgresql_where=text("status = 'published'"),
        ),
        Index(
            "ix_claims_published_category",
            "category_id",
            "id",
            postgresql_where=text("status = 'published'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category_id: Mapped[int | None] = mapped_column(
        ForeignKey("consumption_categories.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", server_default="draft"
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    support_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    report_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    moderation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    hidden_by: Mapped[str | None] = mapped_column(String(8), nullable=True)  # 'auto' | 'human'
    moderation_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class ClaimSupport(Base):
    __tablename__ = "claim_supports"
    __table_args__ = (
        PrimaryKeyConstraint("claim_id", "user_id", name="pk_claim_supports"),
        Index("ix_claim_supports_user", "user_id", "claim_id"),
    )

    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ClaimComment(CreatedAtMixin, Base):
    __tablename__ = "claim_comments"
    __table_args__ = (
        CheckConstraint("char_length(body) BETWEEN 1 AND 1000", name="ck_claim_comments_body_len"),
        CheckConstraint("status IN ('visible','hidden')", name="ck_claim_comments_status"),
        CheckConstraint("report_count >= 0", name="ck_claim_comments_report_count"),
        UniqueConstraint("author_id", "fingerprint", name="uq_claim_comments_author_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=False, index=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="visible", server_default="visible"
    )
    report_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    moderation_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class ContentReport(CreatedAtMixin, Base):
    __tablename__ = "content_reports"
    __table_args__ = (
        CheckConstraint(
            "(claim_id IS NULL) <> (comment_id IS NULL)", name="ck_content_reports_one_target"
        ),
        CheckConstraint(
            "reason IN ('illegal','hate_harassment','off_topic','duplicate','other')",
            name="ck_content_reports_reason",
        ),
        Index(
            "uq_content_reports_claim",
            "reporter_id",
            "claim_id",
            unique=True,
            postgresql_where=text("claim_id IS NOT NULL"),
        ),
        Index(
            "uq_content_reports_comment",
            "reporter_id",
            "comment_id",
            unique=True,
            postgresql_where=text("comment_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporter_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    claim_id: Mapped[int | None] = mapped_column(
        ForeignKey("claims.id", ondelete="CASCADE"), nullable=True
    )
    comment_id: Mapped[int | None] = mapped_column(
        ForeignKey("claim_comments.id", ondelete="CASCADE"), nullable=True
    )
    reason: Mapped[str] = mapped_column(String(24), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
