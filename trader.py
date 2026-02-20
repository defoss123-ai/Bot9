import asyncio
from datetime import datetime
import logging

import aiosqlite
import ccxt.async_support as ccxt


class Trader:
    def __init__(self, exchange, pair_manager, db, logger: logging.Logger, config_manager=None):
        self.exchange: ccxt.Exchange = exchange
        self.pair_manager = pair_manager
        self.db: aiosqlite.Connection = db
        self.logger = logger
        self.config_manager = config_manager
        self.active_orders: dict[str, dict] = {}

    async def get_balance_usdt(self):
        balance = await self.exchange.fetch_balance()
        return float(balance.get("USDT", {}).get("free", 0.0))

    async def get_current_price(self, symbol):
        ticker = await self.exchange.fetch_ticker(symbol)
        return float(ticker.get("last") or 0.0)

    async def calculate_quantity(self, symbol, price):
        risk_percent = 10.0
        if self.config_manager:
            risk_percent = float(await self.config_manager.get("risk_per_trade", 10.0))

        balance = await self.get_balance_usdt()
        spend_amount = balance * (risk_percent / 100.0)
        quantity = spend_amount / price if price > 0 else 0.0

        market = self.exchange.market(symbol)
        if market.get("precision", {}).get("amount") is not None:
            quantity = self.exchange.amount_to_precision(symbol, quantity)

        return float(quantity)

    async def place_limit_order(self, symbol, side, cancel_after):
        settings = self.pair_manager.get_pair_settings(symbol)
        if not settings:
            raise ValueError(f"Настройки для {symbol} не найдены")

        price = await self.get_current_price(symbol)
        if price <= 0:
            raise ValueError(f"Не удалось получить цену для {symbol}")

        if side == "LONG":
            limit_price = price * 0.999
            order_side = "buy"
        else:
            limit_price = price * 1.001
            order_side = "sell"

        quantity = await self.calculate_quantity(symbol, limit_price)
        order = await self.exchange.create_limit_order(symbol, order_side, quantity, limit_price)
        order_id = str(order.get("id"))

        self.active_orders[order_id] = {
            "symbol": symbol,
            "side": side,
            "price": limit_price,
            "quantity": quantity,
            "created_at": datetime.utcnow(),
            "cancel_after": cancel_after,
        }

        await self.db.execute(
            "INSERT INTO orders (id, symbol, side, type, price, amount, status, created_at, cancel_after) VALUES (?,?,?,?,?,?,?,?,?)",
            (order_id, symbol, side, "limit", limit_price, quantity, "open", datetime.utcnow(), cancel_after),
        )
        await self.db.commit()

        self.logger.info("Ордер %s на %s %s %s по %s выставлен", order_id, symbol, side, quantity, limit_price)

        if cancel_after > 0:
            asyncio.create_task(self._auto_cancel(order_id, cancel_after))

        return order_id

    async def _auto_cancel(self, order_id, delay):
        await asyncio.sleep(delay)
        try:
            order = await self.exchange.fetch_order(order_id)
            if order.get("status") == "open":
                await self.exchange.cancel_order(order_id)
                self.logger.info("Ордер %s отменён по таймауту", order_id)
                await self.db.execute("UPDATE orders SET status = 'canceled' WHERE id = ?", (order_id,))
                await self.db.commit()
                self.active_orders.pop(order_id, None)
        except Exception as exc:
            self.logger.error("Ошибка при автоотмене ордера %s: %s", order_id, exc)

    async def has_open_position(self, symbol):
        balance = await self.exchange.fetch_balance()
        base_currency = symbol.split("/")[0] if "/" in symbol else symbol.replace("USDT", "")
        return float(balance.get(base_currency, {}).get("free", 0.0)) > 0

    async def check_positions_and_orders(self):
        while True:
            try:
                for order_id in list(self.active_orders.keys()):
                    try:
                        order = await self.exchange.fetch_order(order_id)
                        if order.get("status") == "closed":
                            self.logger.info("Ордер %s исполнился", order_id)
                            await self.db.execute("UPDATE orders SET status = 'closed' WHERE id = ?", (order_id,))
                            await self.db.commit()
                            del self.active_orders[order_id]
                    except Exception as exc:
                        self.logger.error("Ошибка при проверке ордера %s: %s", order_id, exc)

                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self.logger.error("Ошибка в check_positions_and_orders: %s", exc)
                await asyncio.sleep(10)

    async def close_position(self, symbol):
        balance = await self.exchange.fetch_balance()
        base_currency = symbol.split("/")[0] if "/" in symbol else symbol.replace("USDT", "")
        quantity = float(balance.get(base_currency, {}).get("free", 0.0))

        if quantity <= 0:
            return False

        await self.exchange.create_market_sell_order(symbol, quantity)
        self.logger.info("Позиция по %s закрыта, продано %s", symbol, quantity)
        return True
