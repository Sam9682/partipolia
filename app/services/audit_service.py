"""Service de journal d'audit (Exigence 29).

Ce Service implémente le contrat ``AuditService`` de la conception :

* ``record`` — consigne une entrée immuable dans le Journal_D_Audit
  (table ``audit_logs``) décrivant une action sensible : ``action``,
  ``entity_type``, ``entity_id`` ainsi que les états ``old_data`` / ``new_data``
  en jsonb (Exigence 29.1) ;

Principe de confidentialité (Exigence 29.2) :

* Seule l'empreinte hachée de l'adresse IP (``ip_hash``) est stockée ;
  **l'adresse IP brute n'est JAMAIS conservée**. Le Service reçoit directement
  un ``ip_hash`` déjà calculé par l'appelant et ne manipule pas l'IP en clair.

Le Journal_D_Audit est **append-only** : les entrées ne sont ni modifiées ni
supprimées après écriture.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit import AuditLog


class AuditService:
    """Logique de consignation des actions sensibles (Exigence 29).

    Le Service est instancié par requête avec une ``AsyncSession``.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------ #
    # Consignation d'une action sensible (Exigences 29.1, 29.2)          #
    # ------------------------------------------------------------------ #
    async def record(
        self,
        *,
        action: str,
        entity_type: str,
        entity_id: int | None = None,
        old_data: dict[str, Any] | None = None,
        new_data: dict[str, Any] | None = None,
        ip_hash: str | None = None,
    ) -> AuditLog:
        """Enregistre une entrée immuable dans le Journal_D_Audit.

        Consigne l'``action`` réalisée sur l'entité ``entity_type``/``entity_id``
        avec ses états ``old_data`` / ``new_data`` en jsonb (Exigence 29.1).

        Seul ``ip_hash`` est persisté : l'adresse IP brute n'est jamais stockée
        (Exigence 29.2). L'appelant est responsable de fournir une empreinte déjà
        hachée — aucune IP en clair ne transite par ce Service.
        """
        entry = AuditLog(
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            old_data=old_data,
            new_data=new_data,
            ip_hash=ip_hash,
        )
        self._session.add(entry)
        # Flush pour matérialiser l'id et rendre l'entrée disponible dans la
        # transaction courante, sans committer (géré au niveau de la requête).
        await self._session.flush()
        return entry
