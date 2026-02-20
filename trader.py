import asyncio
import logging

import aiosqlite
import ccxt.async_support as ccxt

from pair_manager import PairManager


class Trader:
    def __init__(
        self,
        exchange: ccxt.Exchange,
        pair_manager: PairManager,
        db: aiosqlite.Connection,
        logger: logging.Logger,
    ) -> None:
        self.exchange = exchange
        self.pair_manager = pair_manager
        self.db = db
        self.logger = logger

    async def has_open_position(self, symbol: str) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM positions WHERE symbol = ? AND status = 'open' LIMIT 1", (symbol,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def place_limit_order(self, symbol: str, side: str, cancel_after: int) -> str:
        order = await self.exchange.create_limit_order(symbol, side.lower(), 0.0, 0.0)
        order_id = str(order.get("id", ""))
        await self.db.execute(
            "INSERT OR REPLACE INTO orders (id, symbol, side, type, price, amount, status, created_at, cancel_after) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
            (order_id, symbol, side, "limit", 0.0, 0.0, order.get("status", "open"), cancel_after),
        )
        await self.db.commit()
        return order_id

    async def check_positions_and_orders(self) -> None:
        while True:
            await asyncio.sleep(10)
