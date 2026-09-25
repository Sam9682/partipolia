"""Service de modération simple des Commentaires — Moteur_De_Modération (Exigence 17).

Ce Service implémente le contrat ``ModerationService`` de la conception : la
méthode :meth:`ModerationService.moderate` applique à un Commentaire soumis un
**filtre automatique** puis une **classification** binaire ``conforme`` /
``douteux`` (Exigence 17.1), puis en tire les conséquences :

* ``conforme`` ⇒ le Commentaire est publié avec le statut ``VISIBLE``
  (Exigence 17.2) ;
* ``douteux`` ⇒ le Commentaire est placé dans la File_De_Modération avec le
  statut ``PENDING``, **sans publication** (Exigence 17.3).

Chaque décision — quelle qu'en soit l'issue — est consignée dans le
Journal_D_Audit via :class:`~app.services.audit_service.AuditService` afin de
rendre la modération auditable (Exigence 17.4).

Conformément à la portée V1 (requirements.md), la modération est **simple** : un
filtre par liste de termes interdits suffit à la classification. Il n'y a ni tri
IA à trois niveaux ni scoring comportemental (design.md, section
ModerationService).

Le Service est instancié par requête avec une ``AsyncSession`` et l'``AuditService``
partageant la même session ; comme les autres Services, il ``flush`` sans
``commit`` (la transaction est gérée au niveau de la requête).
"""

from __future__ import annotations

import re
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.comment import Comment
from app.schemas.moderation import ModerationClassification, ModerationDecision
from app.services.audit_service import AuditService

# Statuts appliqués selon la classification (cf. app.models.comment.COMMENT_STATUSES).
_STATUS_VISIBLE: Final = "VISIBLE"
_STATUS_PENDING: Final = "PENDING"

# Action consignée dans le Journal_D_Audit pour chaque décision (Exigence 17.4).
MODERATION_AUDIT_ACTION: Final = "COMMENT_MODERATED"
# ``entity_type`` de l'entité auditée.
_AUDIT_ENTITY_TYPE: Final = "Comment"

# Liste (minimale, V1) de termes interdits déclenchant la classification
# ``douteux``. La modération V1 est volontairement simple (requirements.md) : un
# filtre lexical par mots suffit. La liste peut être enrichie ultérieurement sans
# changer la logique de classification.
_BLOCKLIST: Final[frozenset[str]] = frozenset(
    {
        "insulte",
        "injure",
        "spam",
        "arnaque",
        "haine",
    }
)

# Découpage en mots (lettres unicode), insensible à la casse, afin d'appliquer le
# filtre sur des termes entiers plutôt que sur des sous-chaînes.
_WORD_RE: Final = re.compile(r"\w+", re.UNICODE)


class ModerationService:
    """Moteur_De_Modération : filtre automatique + classification (Exigence 17).

    Le Service est instancié par requête avec une ``AsyncSession``. Il délègue la
    consignation des décisions à un :class:`AuditService` adossé à la même session
    (Exigence 17.4).
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._audit = AuditService(session)

    # ------------------------------------------------------------------ #
    # Modération d'un Commentaire (Exigences 17.1, 17.2, 17.3, 17.4)     #
    # ------------------------------------------------------------------ #
    async def moderate(
        self,
        comment: Comment,
        *,
        ip_hash: str | None = None,
    ) -> ModerationDecision:
        """Applique le filtre automatique puis la classification d'un Commentaire.

        Enchaîne les étapes de l'Exigence 17 :

        1. **Filtre + classification** (Exigence 17.1) : le contenu est classé
           ``conforme`` ou ``douteux`` selon la présence d'un terme interdit ;
        2. **Publication** (Exigence 17.2) : si ``conforme``, le statut du
           Commentaire passe à ``VISIBLE`` ;
        3. **File_De_Modération** (Exigence 17.3) : si ``douteux``, le statut passe
           à ``PENDING`` et le Commentaire n'est **pas** publié ;
        4. **Audit** (Exigence 17.4) : la décision est consignée dans le
           Journal_D_Audit, quelle qu'en soit l'issue.

        ``ip_hash`` est une empreinte déjà hachée de l'IP de l'auteur, transmise
        telle quelle au Journal_D_Audit (jamais l'IP en clair, Exigence 29.2).
        Retourne la :class:`ModerationDecision` décrivant l'issue.
        """
        classification, reason = self._classify(comment.content)

        if classification is ModerationClassification.CONFORME:
            # Conforme ⇒ publication VISIBLE (Exigence 17.2).
            new_status = _STATUS_VISIBLE
            published = True
        else:
            # Douteux ⇒ File_De_Modération (PENDING), sans publication (Exigence 17.3).
            new_status = _STATUS_PENDING
            published = False

        previous_status = comment.status
        comment.status = new_status
        self._session.add(comment)
        await self._session.flush()

        # Consignation de la décision dans le Journal_D_Audit (Exigence 17.4).
        await self._audit.record(
            action=MODERATION_AUDIT_ACTION,
            entity_type=_AUDIT_ENTITY_TYPE,
            entity_id=comment.id,
            old_data={"status": previous_status},
            new_data={
                "status": new_status,
                "classification": classification.value,
                "published": published,
                "reason": reason,
            },
            ip_hash=ip_hash,
        )

        return ModerationDecision(
            comment_id=comment.id,
            classification=classification,
            status=new_status,
            published=published,
            reason=reason,
        )

    # ------------------------------------------------------------------ #
    # Filtre automatique + classification (Exigence 17.1)                #
    # ------------------------------------------------------------------ #
    def _classify(
        self, content: str
    ) -> tuple[ModerationClassification, str | None]:
        """Classe un contenu en ``conforme`` / ``douteux`` (Exigence 17.1).

        Applique le filtre automatique : le contenu est découpé en mots
        (insensible à la casse) et confronté à la liste de termes interdits. Le
        premier terme interdit rencontré classe le Commentaire ``douteux`` et
        fournit le motif ; sinon le Commentaire est ``conforme`` (``reason`` à
        ``None``).
        """
        for word in _WORD_RE.findall(content.lower()):
            if word in _BLOCKLIST:
                return ModerationClassification.DOUTEUX, word
        return ModerationClassification.CONFORME, None
