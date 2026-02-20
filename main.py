import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
import ccxt.async_support as ccxt
from cryptography.fernet import Fernet
from dotenv import load_dotenv

from db_logger import DBLogHandler, DatabaseLogger
from pair_manager import PairManager
from signal_generator import SignalGenerator
from trader import Trader
from web_interface import WebInterface

load_dotenv()

DB_PATH = Path("trading_bot.db")
MASTER_KEY_PATH = Path("master.key")


class EncryptedSettings:
    def __init__(self, db: aiosqlite.Connection, fernet: Fernet) -> None:
        self.db = db
        self.fernet = fernet

    async def set_api_keys(self, api_key: str, secret: str) -> None:
        encrypted_key = self.fernet.encrypt(api_key.encode()).decode()
        encrypted_secret = self.fernet.encrypt(secret.encode()).decode()
        await self.db.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            [("api_key", encrypted_key), ("api_secret", encrypted_secret)],
        )
        await self.db.commit()

    async def get_api_keys(self) -> tuple[str | None, str | None]:
        cursor = await self.db.execute(
            "SELECT key, value FROM settings WHERE key IN ('api_key', 'api_secret')"
        )
        rows = await cursor.fetchall()
        await cursor.close()

        as_map = {k: v for k, v in rows}
        if "api_key" not in as_map or "api_secret" not in as_map:
            return None, None

        try:
            return (
                self.fernet.decrypt(as_map["api_key"].encode()).decode(),
                self.fernet.decrypt(as_map["api_secret"].encode()).decode(),
            )
        except Exception:
            return None, None


def get_or_create_fernet_key(path: Path = MASTER_KEY_PATH) -> Fernet:
    if path.exists():
        key = path.read_bytes()
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return Fernet(key)

    key = Fernet.generate_key()
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as file_obj:
        file_obj.write(key)
    return Fernet(key)


async def init_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS pairs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE NOT NULL,
            enabled BOOLEAN DEFAULT 1,
            leverage INTEGER DEFAULT 10,
            tp_percent REAL DEFAULT 2.0,
            sl_percent REAL DEFAULT 1.0,
            cancel_time INTEGER DEFAULT 60,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            level TEXT,
            message TEXT
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT CHECK(side IN ('LONG','SHORT')),
            entry_price REAL,
            quantity REAL,
            status TEXT DEFAULT 'open',
            opened_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        """
    )
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS orders (
            id TEXT PRIMARY KEY,
            symbol TEXT,
            side TEXT,
            type TEXT,
            price REAL,
            amount REAL,
            status TEXT,
            created_at TIMESTAMP,
            cancel_after INTEGER
        )
        """
    )
    await db.commit()
    return db


async def create_exchange(api_key: str, secret: str) -> ccxt.Exchange:
    exchange = ccxt.mexc(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    await exchange.fetch_balance()
    return exchange


async def test_connection(api_key: str, secret: str) -> tuple[bool, str]:
    exchange = ccxt.mexc(
        {
            "apiKey": api_key,
            "secret": secret,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        }
    )
    try:
        await exchange.fetch_balance()
        return True, "OK"
    except Exception as exc:
        return False, f"Ошибка: {exc}"
    finally:
        await exchange.close()


@dataclass
class BotContext:
    exchange: ccxt.Exchange | None
    db: aiosqlite.Connection
    logger: logging.Logger
    fernet: Fernet
    running: bool
    tasks: list[asyncio.Task] = field(default_factory=list)
    pair_manager: PairManager | None = None
    signal_generator: SignalGenerator | None = None
    trader: Trader | None = None
    db_logger: DatabaseLogger | None = None
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)

    async def shutdown(self) -> None:
        self.running = False
        self.stop_event.set()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()

        if self.exchange is not None:
            await self.exchange.close()
            self.exchange = None


async def main() -> None:
    logger = logging.getLogger("bot")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler("bot.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    logger.addHandler(stream_handler)

    root = logging.getLogger()
    root.setLevel(logging.INFO)

    db = await init_db()
    fernet = get_or_create_fernet_key()
    encrypted_settings = EncryptedSettings(db, fernet)
    api_key, api_secret = await encrypted_settings.get_api_keys()

    pair_manager = PairManager(db, logger)
    await pair_manager.load_pairs()

    db_logger = DatabaseLogger(db, "operations.log")
    log_queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    queue_handler = DBLogHandler(log_queue)
    queue_handler.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
    root.addHandler(queue_handler)

    context = BotContext(exchange=None, db=db, logger=logger, fernet=fernet, running=True)
    context.pair_manager = pair_manager
    context.db_logger = db_logger

    async def log_processor() -> None:
        while context.running:
            level, message = await log_queue.get()
            await db_logger.log(level, message)

    async def signal_loop() -> None:
        while context.running:
            try:
                active_pairs = pair_manager.get_active_pairs()
                for symbol in active_pairs:
                    if context.trader and context.signal_generator:
                        if not await context.trader.has_open_position(symbol):
                            signal_name = await context.signal_generator.generate_signal(symbol)
                            if signal_name:
                                settings = pair_manager.get_pair_settings(symbol) or {}
                                await context.trader.place_limit_order(
                                    symbol,
                                    signal_name,
                                    int(settings.get("cancel_time", 60)),
                                )
                    await asyncio.sleep(0.5)
                await asyncio.sleep(60)
            except Exception:
                logger.exception("Ошибка в signal_loop")
                await asyncio.sleep(5)

    context.tasks.append(asyncio.create_task(log_processor()))

    if api_key and api_secret:
        try:
            exchange = await create_exchange(api_key, api_secret)
            context.exchange = exchange
            context.signal_generator = SignalGenerator(exchange, logger)
            context.trader = Trader(exchange, pair_manager, db, logger)
            context.tasks.append(asyncio.create_task(context.trader.check_positions_and_orders()))
            context.tasks.append(asyncio.create_task(signal_loop()))
            logger.info("Подключение к MEXC успешно")
        except Exception as exc:
            logger.error("Не удалось создать exchange: %s", exc)
    else:
        logger.warning("API ключи не найдены. Запущен только веб-интерфейс для настройки.")

    web = WebInterface(pair_manager, context.trader, db_logger, context, fernet)
    context.tasks.append(asyncio.create_task(web.run(host="0.0.0.0", port=5000)))

    def request_shutdown() -> None:
        context.running = False
        context.stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            pass

    try:
        while context.running:
            await asyncio.sleep(1)
    finally:
        await context.shutdown()
        await db.close()
        root.removeHandler(queue_handler)
        logger.info("Бот остановлен")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
