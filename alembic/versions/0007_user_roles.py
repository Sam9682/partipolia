"""Add ``users.role`` and migrate legacy administrators to ``super_admin``.

Revision ID: 0007_user_roles
Revises: 0006_consumption_categories
Create Date: 2026-11-07 00:10:00.000000

Additive. ``is_admin`` is kept for the archived legacy modules; ``role`` is the
authoritative field for Partipolia.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007_user_roles"
down_revision: str | None = "0006_consumption_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = "('super_admin','data_admin','content_admin','moderator','citizen')"


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("role", sa.String(length=32), server_default="citizen", nullable=False),
    )
    op.execute("UPDATE users SET role = 'super_admin' WHERE is_admin")
    op.create_check_constraint("ck_users_role", "users", f"role IN {_ROLES}")
    op.create_index("ix_users_role", "users", ["role"])


def downgrade() -> None:
    op.drop_index("ix_users_role", table_name="users")
    op.drop_constraint("ck_users_role", "users", type_="check")
    op.drop_column("users", "role")
