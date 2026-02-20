from quart import Quart, jsonify, render_template


class WebInterface:
    def __init__(self, pair_manager, trader, db_logger, context, fernet):
        self.pair_manager = pair_manager
        self.trader = trader
        self.db_logger = db_logger
        self.context = context
        self.fernet = fernet
        self.app = Quart(__name__)
        self.register_routes()

    def register_routes(self):
        @self.app.get("/")
        async def index():
            return await render_template("index.html")

        @self.app.get("/api/pairs")
        async def api_pairs():
            return jsonify(self.pair_manager.pairs)

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
