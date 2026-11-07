"""Add ``aggregated_impacts.snapshot_date`` with a unique snapshot index.

Revision ID: 0010_aggregate_snapshot_date
Revises: 0009_commitments_and_aggregates
Create Date: 2026-11-08 00:00:00.000000

Adds ``snapshot_date`` (UTC date of ``calculated_at``), removes duplicate
snapshots (keeping the highest ``id`` per group) and creates the unique index
used by ``INSERT ... ON CONFLICT DO UPDATE`` plus a read index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_aggregate_snapshot_date"
down_revision: str | None = "0009_commitments_and_aggregates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("aggregated_impacts", sa.Column("snapshot_date", sa.Date(), nullable=True))
    op.execute(
        "UPDATE aggregated_impacts "
        "SET snapshot_date = (calculated_at AT TIME ZONE 'UTC')::date"
    )
    op.execute(
        """
        DELETE FROM aggregated_impacts a
        USING aggregated_impacts b
        WHERE a.aggregation_period = b.aggregation_period
          AND a.geographical_level = b.geographical_level
          AND a.geographical_code = b.geographical_code
          AND a.snapshot_date = b.snapshot_date
          AND a.id < b.id
        """
    )
    op.alter_column("aggregated_impacts", "snapshot_date", nullable=False)
    op.create_index(
        "uq_aggregated_impacts_snapshot",
        "aggregated_impacts",
        ["aggregation_period", "geographical_level", "geographical_code", "snapshot_date"],
        unique=True,
    )
    op.create_index(
        "ix_aggregated_impacts_level_code_date",
        "aggregated_impacts",
        [
            "geographical_level",
            "geographical_code",
            sa.text("snapshot_date DESC"),
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_aggregated_impacts_level_code_date", table_name="aggregated_impacts")
    op.drop_index("uq_aggregated_impacts_snapshot", table_name="aggregated_impacts")
    op.drop_column("aggregated_impacts", "snapshot_date")
