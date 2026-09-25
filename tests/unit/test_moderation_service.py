"""Tests unitaires du ``ModerationService`` — Moteur_De_Modération (Exigence 17).

Couvre le filtre automatique et la classification simple des Commentaires :

* filtre automatique puis classification ``conforme`` / ``douteux`` (Exigence 17.1) ;
* ``conforme`` ⇒ publication avec le statut ``VISIBLE`` (Exigence 17.2) ;
* ``douteux`` ⇒ File_De_Modération (``PENDING``) sans publication (Exigence 17.3) ;
* chaque décision consignée dans le Journal_D_Audit (Exigence 17.4).

La logique testée (filtre lexical, application du statut, journalisation) est
celle du Service ; seule la couche de persistance est remplacée par une session
factice qui collecte les objets ajoutés, sans mocker la classification elle-même.
"""

from __future__ import annotations

import pytest

from app.models.audit import AuditLog
from app.models.comment import Comment
from app.schemas.moderation import ModerationClassification
from app.services.moderation_service import (
    MODERATION_AUDIT_ACTION,
    ModerationService,
)


class _FakeSession:
    """``AsyncSession`` factice : collecte les objets ``add`` et attribue un id.

    Elle ne remplace aucune logique du Service ; elle imite le minimum d'une
    session (``add`` / ``flush``) et attribue un identifiant au Commentaire lors
    du premier ``flush`` (comme le ferait une vraie base au moment du flush).
    """

    def __init__(self) -> None:
        self.added: list[object] = []
        self._next_id = 1

    def add(self, obj: object) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        # Matérialise un id pour les Commentaires nouvellement ajoutés, comme le
        # ferait une base au flush.
        for obj in self.added:
            if isinstance(obj, Comment) and obj.id is None:
                obj.id = self._next_id
                self._next_id += 1

    @property
    def audit_entries(self) -> list[AuditLog]:
        return [obj for obj in self.added if isinstance(obj, AuditLog)]


def _comment(content: str, *, status: str = "PENDING") -> Comment:
    """Construit un Commentaire soumis (id non encore attribué)."""
    comment = Comment(proposal_id=1, author_id=1, content=content, status=status)
    comment.id = None  # type: ignore[assignment]
    return comment


@pytest.mark.unit
async def test_conforme_comment_is_published_visible() -> None:
    """Un Commentaire sans terme interdit est conforme et publié VISIBLE (Exigences 17.1, 17.2)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("Une contribution constructive au débat.")

    decision = await service.moderate(comment)

    assert decision.classification is ModerationClassification.CONFORME
    assert decision.status == "VISIBLE"
    assert decision.published is True
    assert decision.reason is None
    assert comment.status == "VISIBLE"


@pytest.mark.unit
async def test_douteux_comment_goes_to_moderation_queue_unpublished() -> None:
    """Un Commentaire avec terme interdit est douteux, PENDING et non publié (Exigences 17.1, 17.3)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("Ceci est une insulte manifeste.")

    decision = await service.moderate(comment)

    assert decision.classification is ModerationClassification.DOUTEUX
    assert decision.status == "PENDING"
    assert decision.published is False
    assert decision.reason == "insulte"
    # File_De_Modération : le Commentaire reste PENDING, non publié.
    assert comment.status == "PENDING"


@pytest.mark.unit
async def test_classification_is_case_insensitive() -> None:
    """Le filtre est insensible à la casse (Exigence 17.1)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("SPAM à répétition")

    decision = await service.moderate(comment)

    assert decision.classification is ModerationClassification.DOUTEUX
    assert decision.reason == "spam"


@pytest.mark.unit
async def test_blocklist_matches_whole_words_only() -> None:
    """Le filtre porte sur des mots entiers, pas sur des sous-chaînes (Exigence 17.1)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    # "spammeur" contient "spam" mais n'est pas le mot "spam" : reste conforme.
    comment = _comment("Le mot spammeur ne doit pas déclencher le filtre.")

    decision = await service.moderate(comment)

    assert decision.classification is ModerationClassification.CONFORME
    assert comment.status == "VISIBLE"


@pytest.mark.unit
async def test_conforme_decision_is_audited() -> None:
    """Une décision conforme est consignée dans le Journal_D_Audit (Exigence 17.4)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("Contribution parfaitement conforme.")

    decision = await service.moderate(comment)

    entries = session.audit_entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == MODERATION_AUDIT_ACTION
    assert entry.entity_type == "Comment"
    assert entry.entity_id == comment.id
    assert entry.new_data is not None
    assert entry.new_data["classification"] == "conforme"
    assert entry.new_data["status"] == "VISIBLE"
    assert entry.new_data["published"] is True
    assert decision.comment_id == comment.id


@pytest.mark.unit
async def test_douteux_decision_is_audited_with_reason() -> None:
    """Une décision douteuse est auditée avec son motif (Exigence 17.4)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("Message de haine ciblée.")

    await service.moderate(comment)

    entries = session.audit_entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.action == MODERATION_AUDIT_ACTION
    assert entry.new_data is not None
    assert entry.new_data["classification"] == "douteux"
    assert entry.new_data["status"] == "PENDING"
    assert entry.new_data["published"] is False
    assert entry.new_data["reason"] == "haine"


@pytest.mark.unit
async def test_audit_records_previous_status_and_ip_hash() -> None:
    """L'audit conserve l'ancien statut et l'empreinte IP fournie (Exigences 17.4, 29.2)."""
    session = _FakeSession()
    service = ModerationService(session)  # type: ignore[arg-type]
    comment = _comment("Une insulte gratuite.", status="PENDING")

    await service.moderate(comment, ip_hash="abc123hash")

    entry = session.audit_entries[0]
    assert entry.old_data == {"status": "PENDING"}
    assert entry.ip_hash == "abc123hash"
