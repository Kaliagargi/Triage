import asyncpg
from app.config import settings

class DatabaseManager:
    pool: asyncpg.Pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(settings.DATABASE_URL, min_size=5, max_size=20)

    async def disconnect(self):
        if self.pool:
            await self.pool.close()

db_manager = DatabaseManager()