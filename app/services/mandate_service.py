"""Service de suivi du mandat (Exigence 21).

Ce Service implémente le contrat ``MandateService`` de la conception :

* ``list_commitments`` / ``list_indicators`` — listes publiques des Engagements et
  des Indicateurs (Exigences 21.1, 21.3) ;
* ``upsert_commitment`` — crée ou met à jour un Engagement dont le ``status`` est
  contraint aux 5 valeurs autorisées (Exigences 21.1, 21.2) ;
* ``update_indicator`` — met à jour la ``current_value`` d'un Indicateur
  (Exigence 21.4).

Chaque mise à jour de la valeur d'un Indicateur ou du statut d'un Engagement est
consignée dans le Journal_D_Audit via l':class:`~app.services.audit_service.AuditService`
(Exigence 21.4). La consultation (``list_*``) est publique et n'écrit rien.

Le ``status`` fourni est validé contre l'ensemble ``COMMITMENT_STATUSES`` défini
par le modèle (Exigence 21.2). Le Service est instancié par requête avec une
``AsyncSession`` (SQLAlchemy 2.x async).
"""

from __future__ import annotations

from datetime import date
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.mandate import COMMITMENT_STATUSES, Commitment, Indicator
from app.services.audit_service import AuditService

# Messages d'erreur génériques (Exigences 31.1, 31.2).
COMMITMENT_NOT_FOUND_ERROR: Final = "Engagement introuvable."
INDICATOR_NOT_FOUND_ERROR: Final = "Indicateur introuvable."
INVALID_COMMITMENT_STATUS_ERROR: Final = "Statut d'engagement invalide."

# Libellés d'action consignés dans le Journal_D_Audit (Exigence 21.4).
_ACTION_CREATE_COMMITMENT: Final = "COMMITMENT_CREATE"
_ACTION_UPDATE_COMMITMENT: Final = "COMMITMENT_UPDATE"
_ACTION_UPDATE_INDICATOR: Final = "INDICATOR_UPDATE"


class MandateError(Exception):
    """Erreur générique du Service de suivi du mandat."""


class CommitmentNotFoundError(MandateError):
    """L'Engagement référencé n'existe pas (Exigence 31.2)."""


class IndicatorNotFoundError(MandateError):
    """L'Indicateur référencé n'existe pas (Exigence 31.2)."""


class InvalidCommitmentStatusError(MandateError):
    """Le ``status`` fourni n'appartient pas aux 5 valeurs autorisées (Exigence 21.2)."""


class MandateService:
    """Gestion des Engagements et des Indicateurs du mandat (Exigence 21)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # Listes publiques (Exigences 21.1, 21.3)                             #
    # ------------------------------------------------------------------ #
    async def list_commitments(self) -> list[Commitment]:
        """Retourne tous les Engagements, ordonnés par identifiant (Exigence 21.1)."""
        result = await self._session.scalars(select(Commitment).order_by(Commitment.id))
        return list(result.all())

    async def list_indicators(self) -> list[Indicator]:
        """Retourne tous les Indicateurs, ordonnés par identifiant (Exigence 21.3)."""
        result = await self._session.scalars(select(Indicator).order_by(Indicator.id))
        return list(result.all())

    # ------------------------------------------------------------------ #
    # Upsert d'un Engagement (Exigences 21.1, 21.2, 21.4)                 #
    # ------------------------------------------------------------------ #
    async def upsert_commitment(
        self,
        *,
        commitment_id: int | None = None,
        title: str,
        status: str,
        target_date: date | None = None,
        progress: float | None = None,
        notes: str | None = None,
        ip_hash: str | None = None,
    ) -> Commitment:
        """Crée ou met à jour un Engagement (Exigences 21.1, 21.2).

        ``commitment_id`` absent ⇒ création ; présent ⇒ mise à jour de
        l'Engagement existant (:class:`CommitmentNotFoundError` sinon). Le
        ``status`` est validé contre les 5 valeurs autorisées
        (:class:`InvalidCommitmentStatusError` sinon, Exigence 21.2). La
        modification du statut est consignée dans le Journal_D_Audit
        (Exigence 21.4).
        """
        if status not in COMMITMENT_STATUSES:
            raise InvalidCommitmentStatusError(status)

        if commitment_id is None:
            commitment = Commitment(
                title=title,
                status=status,
                target_date=target_date,
                progress=progress,
                notes=notes,
            )
            self._session.add(commitment)
            await self._session.flush()
            await self._session.refresh(commitment)

            await self._audit.record(
                action=_ACTION_CREATE_COMMITMENT,
                entity_type="commitment",
                entity_id=commitment.id,
                new_data={"title": title, "status": status},
                ip_hash=ip_hash,
            )
            return commitment

        commitment = await self._session.get(Commitment, commitment_id)
        if commitment is None:
            raise CommitmentNotFoundError(str(commitment_id))

        old_status = commitment.status
        commitment.title = title
        commitment.status = status
        commitment.target_date = target_date
        commitment.progress = progress
        commitment.notes = notes
        await self._session.flush()
        await self._session.refresh(commitment)

        await self._audit.record(
            action=_ACTION_UPDATE_COMMITMENT,
            entity_type="commitment",
            entity_id=commitment.id,
            old_data={"status": old_status},
            new_data={"status": status},
            ip_hash=ip_hash,
        )
        return commitment

    # ------------------------------------------------------------------ #
    # Mise à jour de la valeur d'un Indicateur (Exigence 21.4)            #
    # ------------------------------------------------------------------ #
    async def update_indicator(
        self,
        indicator_id: int,
        *,
        current_value: float,
        ip_hash: str | None = None,
    ) -> Indicator:
        """Met à jour la ``current_value`` d'un Indicateur (Exigence 21.4).

        Lève :class:`IndicatorNotFoundError` si l'Indicateur n'existe pas. La
        modification de la valeur est consignée dans le Journal_D_Audit
        (Exigence 21.4).
        """
        indicator = await self._session.get(Indicator, indicator_id)
        if indicator is None:
            raise IndicatorNotFoundError(str(indicator_id))

        old_value = indicator.current_value
        indicator.current_value = current_value
        await self._session.flush()
        await self._session.refresh(indicator)

        await self._audit.record(
            action=_ACTION_UPDATE_INDICATOR,
            entity_type="indicator",
            entity_id=indicator.id,
            old_data={"current_value": old_value},
            new_data={"current_value": current_value},
            ip_hash=ip_hash,
        )
        return indicator
