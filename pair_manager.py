import logging
from typing import Any

import aiosqlite


class PairManager:
    def __init__(self, db: aiosqlite.Connection, logger: logging.Logger) -> None:
        self.db = db
        self.logger = logger
        self.pairs: dict[str, dict[str, Any]] = {}

    async def load_pairs(self) -> None:
        cursor = await self.db.execute(
            "SELECT symbol, enabled, leverage, tp_percent, sl_percent, cancel_time FROM pairs"
        )
        rows = await cursor.fetchall()
        await cursor.close()
        self.pairs = {
            row[0]: {
                "enabled": bool(row[1]),
                "leverage": int(row[2]),
                "tp_percent": float(row[3]),
                "sl_percent": float(row[4]),
                "cancel_time": int(row[5]),
            }
            for row in rows
        }

    def get_active_pairs(self) -> list[str]:
        return [symbol for symbol, settings in self.pairs.items() if settings.get("enabled")]

    def get_pair_settings(self, symbol: str) -> dict[str, Any] | None:
        return self.pairs.get(symbol)
