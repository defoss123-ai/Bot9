import json

import aiosqlite


class ConfigManager:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db
        self.cache = {"risk_per_trade": 10.0}

    async def init_table(self):
        await self.db.execute(
            '''
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            '''
        )
        await self.db.commit()

    async def get(self, key: str, default=None):
        if key in self.cache:
            return self.cache[key]
        async with self.db.execute('SELECT value FROM config WHERE key = ?', (key,)) as cursor:
            row = await cursor.fetchone()
            if row:
                value = json.loads(row[0])
                self.cache[key] = value
                return value
            return default

    async def set(self, key: str, value):
        json_value = json.dumps(value)
        await self.db.execute('INSERT OR REPLACE INTO config (key, value) VALUES (?, ?)', (key, json_value))
        await self.db.commit()
        self.cache[key] = value

    async def load_all(self):
        async with self.db.execute('SELECT key, value FROM config') as cursor:
            rows = await cursor.fetchall()
            for key, value_json in rows:
                self.cache[key] = json.loads(value_json)
        return self.cache
