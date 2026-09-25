"""Tests unitaires du ``MandateService`` (Exigence 21).

Couvre la logique du Service sans base de données réelle, via une ``AsyncSession``
factice n'implémentant que les opérations utilisées (``get``, ``add``, ``flush``,
``refresh``, ``scalars``). Le vrai code du Service **et** de l'``AuditService`` est
exercé (aucun mock de logique) :

* ``upsert_commitment`` crée un Engagement (status par défaut NOT_STARTED) et
  consigne la création (Exigences 21.1, 21.4) ;
* ``upsert_commitment`` met à jour un Engagement existant et consigne le
  changement de statut (Exigences 21.1, 21.4) ;
* ``upsert_commitment`` refuse un ``status`` hors des 5 valeurs autorisées
  (Exigence 21.2) ;
* ``update_indicator`` met à jour ``current_value`` et consigne la modification
  (Exigence 21.4) ;
* ``list_commitments`` / ``list_indicators`` renvoient les entités ordonnées
  (Exigences 21.1, 21.3).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.models.audit import AuditLog
from app.models.mandate import Commitment, Indicator
from app.services.mandate_service import (
    CommitmentNotFoundError,
    IndicatorNotFoundError,
    InvalidCommitmentStatusError,
    MandateService,
)


class _FakeScalarResult:
    def __init__(self, rows: list[object]) -> None:
        self._rows = rows

    def all(self) -> list[object]:
        return list(self._rows)


class _FakeSession:
    """``AsyncSession`` factice avec magasin en mémoire (engagements, indicateurs, audit)."""

    def __init__(self) -> None:
        self.commitments: dict[int, Commitment] = {}
        self.indicators: dict[int, Indicator] = {}
        self.audit_logs: list[AuditLog] = []
        self._next_commitment_id = 1
        # Le type renvoyé par ``scalars`` alterne selon la dernière entité demandée.
        self._last_query: type | None = None

    async def get(self, model: type, ident: int) -> object | None:
        if model is Commitment:
            return self.commitments.get(ident)
        if model is Indicator:
            return self.indicators.get(ident)
        return None

    def add(self, obj: object) -> None:
        now = datetime.now(timezone.utc)
        if isinstance(obj, Commitment):
            if obj.id is None:
                obj.id = self._next_commitment_id
                self._next_commitment_id += 1
            if getattr(obj, "created_at", None) is None:
                obj.created_at = now
                obj.updated_at = now
            self.commitments[obj.id] = obj
        elif isinstance(obj, AuditLog):
            self.audit_logs.append(obj)

    def seed_indicator(self, indicator: Indicator) -> None:
        now = datetime.now(timezone.utc)
        if getattr(indicator, "created_at", None) is None:
            indicator.created_at = now
            indicator.updated_at = now
        self.indicators[indicator.id] = indicator

    async def flush(self) -> None:
        return None

    async def refresh(self, obj: object) -> None:
        return None

    async def scalars(self, statement: object) -> _FakeScalarResult:
        # Choix de la collection à renvoyer selon l'entité interrogée. On inspecte
        # la représentation textuelle du SELECT pour rester agnostique au dialecte.
        text = str(statement).lower()
        if "indicator" in text:
            rows: list[object] = sorted(self.indicators.values(), key=lambda i: i.id)
        else:
            rows = sorted(self.commitments.values(), key=lambda c: c.id)
        return _FakeScalarResult(rows)


@pytest.mark.unit
async def test_upsert_commitment_creates_with_default_status_and_records_audit() -> None:
    """La création d'un Engagement consigne la modification (Exigences 21.1, 21.4)."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    commitment = await service.upsert_commitment(
        title="Rénover 100 écoles", status="NOT_STARTED"
    )

    assert commitment.id is not None
    assert commitment.status == "NOT_STARTED"
    assert session.audit_logs[-1].action == "COMMITMENT_CREATE"
    assert session.audit_logs[-1].entity_id == commitment.id


@pytest.mark.unit
async def test_upsert_commitment_updates_existing_and_records_status_change() -> None:
    """La mise à jour consigne l'ancien et le nouveau statut (Exigences 21.1, 21.4)."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    created = await service.upsert_commitment(title="Objectif", status="NOT_STARTED")
    updated = await service.upsert_commitment(
        commitment_id=created.id, title="Objectif", status="IN_PROGRESS"
    )

    assert updated.id == created.id
    assert updated.status == "IN_PROGRESS"
    last = session.audit_logs[-1]
    assert last.action == "COMMITMENT_UPDATE"
    assert last.old_data == {"status": "NOT_STARTED"}
    assert last.new_data == {"status": "IN_PROGRESS"}


@pytest.mark.unit
async def test_upsert_commitment_rejects_invalid_status() -> None:
    """Un ``status`` hors des 5 valeurs autorisées est refusé (Exigence 21.2)."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    with pytest.raises(InvalidCommitmentStatusError):
        await service.upsert_commitment(title="X", status="DONE")

    assert session.audit_logs == []


@pytest.mark.unit
async def test_upsert_commitment_missing_id_raises_not_found() -> None:
    """Mettre à jour un Engagement inexistant lève ``CommitmentNotFoundError``."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    with pytest.raises(CommitmentNotFoundError):
        await service.upsert_commitment(
            commitment_id=999, title="X", status="COMPLETED"
        )


@pytest.mark.unit
async def test_update_indicator_records_value_change() -> None:
    """La mise à jour d'un Indicateur consigne l'ancienne et la nouvelle valeur (Exigence 21.4)."""
    session = _FakeSession()
    indicator = Indicator(id=5, name="Chômage", unit="%", current_value=8.0)
    session.seed_indicator(indicator)
    service = MandateService(session)  # type: ignore[arg-type]

    updated = await service.update_indicator(5, current_value=7.2)

    assert updated.current_value == pytest.approx(7.2)
    last = session.audit_logs[-1]
    assert last.action == "INDICATOR_UPDATE"
    assert last.old_data == {"current_value": 8.0}
    assert last.new_data == {"current_value": 7.2}


@pytest.mark.unit
async def test_update_missing_indicator_raises_not_found() -> None:
    """Mettre à jour un Indicateur inexistant lève ``IndicatorNotFoundError``."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    with pytest.raises(IndicatorNotFoundError):
        await service.update_indicator(404, current_value=1.0)


@pytest.mark.unit
async def test_list_commitments_and_indicators_ordered() -> None:
    """Les listes renvoient les entités ordonnées par identifiant (Exigences 21.1, 21.3)."""
    session = _FakeSession()
    service = MandateService(session)  # type: ignore[arg-type]

    await service.upsert_commitment(title="A", status="NOT_STARTED")
    await service.upsert_commitment(title="B", status="COMPLETED")
    session.seed_indicator(Indicator(id=2, name="I2"))
    session.seed_indicator(Indicator(id=1, name="I1"))

    commitments = await service.list_commitments()
    indicators = await service.list_indicators()

    assert [c.title for c in commitments] == ["A", "B"]
    assert [i.id for i in indicators] == [1, 2]
