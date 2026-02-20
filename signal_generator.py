import logging

import ccxt.async_support as ccxt


class SignalGenerator:
    def __init__(self, exchange: ccxt.Exchange, logger: logging.Logger, config_manager) -> None:
        self.exchange = exchange
        self.logger = logger
        self.config_manager = config_manager
    def __init__(
        self,
        exchange: ccxt.Exchange,
        logger: logging.Logger,
        lookback: int = 20,
        volume_multiplier: float = 1.5,
    ) -> None:
        self.exchange = exchange
        self.logger = logger
        self.lookback = lookback
        self.volume_multiplier = volume_multiplier

    async def fetch_ohlcv(self, symbol: str, limit: int = 100) -> list:
        try:
            return await self.exchange.fetch_ohlcv(symbol, timeframe="1m", limit=limit)
        except Exception as exc:
            self.logger.error("fetch_ohlcv failed for %s: %s", symbol, exc)
            return []

    async def generate_signal(self, symbol: str) -> str | None:
        lookback = int(await self.config_manager.get("lookback", 20))
        volume_multiplier = float(await self.config_manager.get("volume_multiplier", 1.5))

        candles = await self.fetch_ohlcv(symbol, limit=lookback + 5)
        if len(candles) < lookback + 1:
            return None

        recent = candles[-(lookback + 1) :]
        current = recent[-1]
        previous = recent[:-1]

        highs_prev = [c[2] for c in previous]
        lows_prev = [c[3] for c in previous]
        volumes_prev = [c[5] for c in previous]
        closes_prev = [c[4] for c in previous]

        local_high = max(highs_prev)
        local_low = min(lows_prev)
        avg_volume = sum(volumes_prev) / len(volumes_prev)
        momentum = closes_prev[-1] - closes_prev[-3] if len(closes_prev) >= 3 else 0

        current_high = current[2]
        current_low = current[3]
        current_volume = current[5]

        if current_high > local_high and current_volume > avg_volume * volume_multiplier and momentum > 0:
            return "LONG"
        if current_low < local_low and current_volume > avg_volume * volume_multiplier and momentum < 0:
            return "SHORT"
        return None
