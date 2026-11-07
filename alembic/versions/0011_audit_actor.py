"""Add ``audit_logs.actor_id`` (FK ``users.id`` ON DELETE SET NULL, indexed).

Revision ID: 0011_audit_actor
Revises: 0010_aggregate_snapshot_date
Create Date: 2026-11-09 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_audit_actor"
down_revision: str | None = "0010_aggregate_snapshot_date"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("actor_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_audit_logs_actor_id_users",
        "audit_logs",
        "users",
        ["actor_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])


def downgrade() -> None:
    op.drop_index("ix_audit_logs_actor_id", table_name="audit_logs")
    op.drop_constraint("fk_audit_logs_actor_id_users", "audit_logs", type_="foreignkey")
    op.drop_column("audit_logs", "actor_id")
