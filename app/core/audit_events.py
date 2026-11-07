"""Catalogue des actions d'audit de sécurité et empreinte d'adresse IP.

Exigences 15.7, 15.8 ; décisions D8 (consignation isolée) et D9 (``ip_hash``).

L'adresse IP brute n'est jamais conservée : seule une empreinte HMAC-SHA256
(clé dérivée du secret JWT) est transmise au Journal_Audit.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Final

from fastapi import Request

from app.core.config import settings

ACTION_LOGIN_FAILED: Final = "auth.login_failed"
ACTION_LOGIN_REPEATED_FAILURES: Final = "auth.login_repeated_failures"
ACTION_ADMIN_ACCESS_DENIED: Final = "admin.access_denied"
ACTION_PRIVACY_EXPORT_REQUESTED: Final = "privacy.export_requested"
ACTION_PRIVACY_DELETE_REQUESTED: Final = "privacy.delete_requested"
ACTION_REGION_DETAIL_DENIED: Final = "dashboard.region_detail_denied"


def hash_ip(ip: str | None) -> str | None:
    """Empreinte HMAC-SHA256 (hex) d'une adresse IP ; ``None`` si absente."""
    if not ip:
        return None
    key = settings.jwt_secret_key.get_secret_value().encode("utf-8")
    return hmac.new(key, ip.encode("utf-8"), hashlib.sha256).hexdigest()


def request_ip_hash(request: Request | None) -> str | None:
    """Empreinte de l'IP source (``X-Forwarded-For`` derrière nginx, sinon le client)."""
    if request is None:
        return None
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return hash_ip(first)
    client = request.client
    if client is not None and client.host:
        return hash_ip(client.host)
    return None
