"""Persistance du dernier statut et revenu net saisis par un utilisateur connecté.

Seuls le statut et le revenu net annuel sont enregistrés (minimisation RGPD).
Aucune de ces valeurs n'est journalisée.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.user_income import UserIncome

logger = get_logger(__name__)

PERSIST_FAILED_EVENT = "user_income_persist_failed"


class UserIncomeService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, user_id: int, statut: str, revenu_net: Decimal) -> None:
        """Upsert atomique (``ON CONFLICT (user_id) DO UPDATE``) dans un SAVEPOINT.

        Un échec est absorbé : seul un événement fixe est journalisé (jamais le
        statut ni le revenu), et la transaction de la requête reste utilisable.
        """
        stmt = pg_insert(UserIncome).values(
            user_id=user_id, statut=statut, revenu_net=revenu_net, updated_at=func.now()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[UserIncome.user_id],
            set_={
                "statut": stmt.excluded.statut,
                "revenu_net": stmt.excluded.revenu_net,
                "updated_at": func.now(),
            },
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(stmt)
        except SQLAlchemyError:
            logger.error(PERSIST_FAILED_EVENT)

    async def get(self, user_id: int) -> UserIncome | None:
        result = await self._session.execute(
            select(UserIncome).where(UserIncome.user_id == user_id)
        )
        return result.scalar_one_or_none()
