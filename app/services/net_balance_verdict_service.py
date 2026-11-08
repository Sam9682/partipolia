"""Persistance du dernier verdict du bilan net d'un utilisateur connecté.

Seuls le verdict, le montant du bilan et la date de calcul sont enregistrés.
Aucune donnée saisie n'est stockée ni journalisée.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.net_balance_verdict import NetBalanceVerdict

logger = get_logger(__name__)

CONTRIBUTEUR_NET = "contributeur_net"
PERSIST_FAILED_EVENT = "net_balance_verdict_persist_failed"


class NetBalanceVerdictService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def record(self, user_id: int, verdict: str, balance: Decimal) -> None:
        """Upsert atomique (``ON CONFLICT (user_id) DO UPDATE``) dans un SAVEPOINT.

        Un échec est absorbé : seul un événement fixe est journalisé (jamais le
        verdict ni le montant), et la transaction de la requête reste utilisable.
        """
        stmt = pg_insert(NetBalanceVerdict).values(
            user_id=user_id, verdict=verdict, balance=balance, computed_at=func.now()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[NetBalanceVerdict.user_id],
            set_={
                "verdict": stmt.excluded.verdict,
                "balance": stmt.excluded.balance,
                "computed_at": func.now(),
            },
        )
        try:
            async with self._session.begin_nested():
                await self._session.execute(stmt)
        except SQLAlchemyError:
            logger.error(PERSIST_FAILED_EVENT)

    async def get(self, user_id: int) -> NetBalanceVerdict | None:
        result = await self._session.execute(
            select(NetBalanceVerdict).where(NetBalanceVerdict.user_id == user_id)
        )
        return result.scalar_one_or_none()

    async def is_net_contributor(self, user_id: int) -> bool:
        """Vrai ssi un verdict existe et vaut ``contributeur_net``."""
        result = await self._session.execute(
            select(NetBalanceVerdict.verdict).where(NetBalanceVerdict.user_id == user_id)
        )
        return result.scalar_one_or_none() == CONTRIBUTEUR_NET
