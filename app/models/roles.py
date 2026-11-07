"""Roles of the Partipolia governance model."""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """User roles. ``SUPER_ADMIN`` implicitly holds every other role."""

    SUPER_ADMIN = "super_admin"
    DATA_ADMIN = "data_admin"
    CONTENT_ADMIN = "content_admin"
    MODERATOR = "moderator"
    CITIZEN = "citizen"


ROLE_VALUES: tuple[str, ...] = tuple(r.value for r in Role)


def has_role(user_role: str, *allowed: Role) -> bool:
    """True if ``user_role`` is one of ``allowed`` or is ``super_admin``."""
    return user_role == Role.SUPER_ADMIN or user_role in {r.value for r in allowed}
