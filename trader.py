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
        config_manager,
    ) -> None:
        self.exchange = exchange
        self.pair_manager = pair_manager
        self.db = db
        self.logger = logger
        self.config_manager = config_manager

    async def has_open_position(self, symbol: str) -> bool:
        cursor = await self.db.execute(
            "SELECT 1 FROM positions WHERE symbol = ? AND status = 'open' LIMIT 1", (symbol,)
        )
        row = await cursor.fetchone()
        await cursor.close()
        return row is not None

    async def calculate_quantity(self, symbol: str, side: str, leverage: int) -> float:
        risk_percent = float(await self.config_manager.get("risk_per_trade", 5.0))
        balance = await self.exchange.fetch_balance()
        free_usdt = float(balance.get("USDT", {}).get("free", 0.0))

        ticker = await self.exchange.fetch_ticker(symbol)
        price = float(ticker.get("last") or 0.0)
        if price <= 0:
            return 0.0

        max_margin = free_usdt * (risk_percent / 100.0)
        quantity = (max_margin * leverage) / price
        return max(quantity, 0.0)

    async def place_limit_order(self, symbol: str, side: str, cancel_after: int) -> str:
        settings = self.pair_manager.get_pair_settings(symbol) or {}
        leverage = int(settings.get("leverage", 10))
        quantity = await self.calculate_quantity(symbol, side, leverage)

        orderbook = await self.exchange.fetch_order_book(symbol)
        if side.upper() == "LONG":
            price = float(orderbook["bids"][0][0]) * 1.001 if orderbook.get("bids") else 0.0
            order_side = "buy"
        else:
            price = float(orderbook["asks"][0][0]) * 0.999 if orderbook.get("asks") else 0.0
            order_side = "sell"

        order = await self.exchange.create_limit_order(symbol, order_side, quantity, price)
        order_id = str(order.get("id", ""))
        await self.db.execute(
            "INSERT OR REPLACE INTO orders (id, symbol, side, type, price, amount, status, created_at, cancel_after) VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?)",
            (order_id, symbol, side, "limit", price, quantity, order.get("status", "open"), cancel_after),
        )
        await self.db.commit()
        return order_id

    async def check_positions_and_orders(self) -> None:
        while True:
            await asyncio.sleep(10)
