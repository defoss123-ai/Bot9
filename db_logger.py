import asyncio
import logging
from datetime import datetime

import aiosqlite


class DatabaseLogger:
    def __init__(self, db: aiosqlite.Connection, log_file: str = "operations.log") -> None:
        self.db = db
        self.log_file = log_file
        self._file_lock = asyncio.Lock()

    async def log(self, level: str, message: str) -> None:
        await self.db.execute("INSERT INTO logs (level, message) VALUES (?, ?)", (level, message))
        await self.db.commit()

        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {level} | {message}\n"
        async with self._file_lock:
            with open(self.log_file, "a", encoding="utf-8") as file_obj:
                file_obj.write(line)

    async def get_recent(self, limit: int = 50, level: str | None = None) -> list[dict]:
        query = "SELECT created_at, level, message FROM logs"
        params: list[object] = []
        if level:
            query += " WHERE level = ?"
            params.append(level)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        cursor = await self.db.execute(query, params)
        rows = await cursor.fetchall()
        await cursor.close()
        return [{"timestamp": r[0], "level": r[1], "message": r[2]} for r in rows]


class DBLogHandler(logging.Handler):
    def __init__(self, queue: asyncio.Queue) -> None:
        super().__init__()
        self.queue = queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait((record.levelname, self.format(record)))
        except Exception:
            self.handleError(record)
