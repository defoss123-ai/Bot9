from __future__ import annotations

import ccxt.async_support as ccxt
from quart import Quart, jsonify, render_template, request


class WebInterface:
    def __init__(self, pair_manager, trader, db_logger, context, fernet, config_manager):
        self.pair_manager = pair_manager
        self.trader = trader
        self.db_logger = db_logger
        self.context = context
        self.fernet = fernet
        self.config_manager = config_manager
        self.app = Quart(__name__)
        self.register_routes()

    def register_routes(self):
        @self.app.get("/")
        async def index():
            return await render_template("index.html")

        @self.app.get("/api/pairs")
        async def api_pairs():
            return jsonify(self.pair_manager.pairs)

        @self.app.post("/api/pairs")
        async def save_pair():
            data = await request.get_json() or {}
            symbol = str(data.get("symbol", "")).upper().strip()
            if not symbol:
                return jsonify({"success": False, "message": "Symbol is required"}), 400

            enabled = bool(data.get("enabled", True))
            leverage = int(data.get("leverage", 10))
            tp_percent = float(data.get("tp_percent", 2.0))
            sl_percent = float(data.get("sl_percent", 1.0))
            cancel_time = int(data.get("cancel_time", 60))

            await self.context.db.execute(
                """
                INSERT OR REPLACE INTO pairs (symbol, enabled, leverage, tp_percent, sl_percent, cancel_time)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (symbol, 1 if enabled else 0, leverage, tp_percent, sl_percent, cancel_time),
            )
            await self.context.db.commit()
            await self.pair_manager.load_pairs()
            return jsonify({"success": True})

        @self.app.post("/api/keys")
        async def save_keys():
            data = await request.get_json() or {}
            api_key = str(data.get("api_key", "")).strip()
            api_secret = str(data.get("api_secret", "")).strip()
            if not api_key or not api_secret:
                return jsonify({"success": False, "message": "API ключи не предоставлены"}), 400

            encrypted_key = self.fernet.encrypt(api_key.encode()).decode()
            encrypted_secret = self.fernet.encrypt(api_secret.encode()).decode()
            await self.context.db.executemany(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                [("api_key", encrypted_key), ("api_secret", encrypted_secret)],
            )
            await self.context.db.commit()
            return jsonify({"success": True, "message": "Ключи сохранены"})

        @self.app.post("/api/test_connection")
        async def test_connection():
            data = await request.get_json() or {}
            api_key = str(data.get("api_key", "")).strip()
            api_secret = str(data.get("api_secret", "")).strip()

            if not api_key or not api_secret:
                return jsonify({"success": False, "message": "API ключи не предоставлены"})

            exchange = None
            try:
                exchange = ccxt.mexc(
                    {
                        "apiKey": api_key,
                        "secret": api_secret,
                        "enableRateLimit": True,
                        "options": {"defaultType": "swap"},
                    }
                )
                await exchange.fetch_balance()
                return jsonify({"success": True, "message": "Подключение успешно"})
            except ccxt.AuthenticationError:
                return jsonify({"success": False, "message": "Ошибка аутентификации: неверные ключи"})
            except Exception as exc:
                return jsonify({"success": False, "message": f"Ошибка: {exc}"})
            finally:
                if exchange is not None:
                    await exchange.close()

        @self.app.get("/api/strategy")
        async def get_strategy():
            return jsonify(
                {
                    "lookback": int(await self.config_manager.get("lookback", 20)),
                    "volume_multiplier": float(await self.config_manager.get("volume_multiplier", 1.5)),
                    "check_interval": int(await self.config_manager.get("check_interval", 60)),
                    "risk_per_trade": float(await self.config_manager.get("risk_per_trade", 5.0)),
                }
            )

        @self.app.post("/api/strategy")
        async def save_strategy():
            data = await request.get_json() or {}
            await self.config_manager.set("lookback", int(data.get("lookback", 20)))
            await self.config_manager.set("volume_multiplier", float(data.get("volume_multiplier", 1.5)))
            await self.config_manager.set("check_interval", int(data.get("check_interval", 60)))
            await self.config_manager.set("risk_per_trade", float(data.get("risk_per_trade", 5.0)))
            return jsonify({"success": True})

        @self.app.get("/api/status")
        async def api_status():
            return jsonify(
                {
                    "running": self.context.running,
                    "active_pairs": len(self.pair_manager.get_active_pairs()),
                    "trader_ready": self.context.trader is not None,
                }
            )

        @self.app.get("/api/logs")
        async def api_logs():
            logs = await self.db_logger.get_recent(limit=50)
            return jsonify(logs)

    async def run(self, host: str = "127.0.0.1", port: int = 5000):
        await self.app.run_task(host=host, port=port)
