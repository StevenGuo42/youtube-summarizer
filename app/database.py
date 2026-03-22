import aiosqlite
from app.config import DB_PATH


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                video_id TEXT NOT NULL,
                title TEXT,
                channel TEXT,
                duration INTEGER,
                thumbnail_url TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                current_step TEXT,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS summaries (
                id TEXT PRIMARY KEY,
                job_id TEXT NOT NULL REFERENCES jobs(id),
                transcript TEXT,
                raw_response TEXT,
                structured_summary TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS llm_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                provider TEXT,
                model TEXT,
                api_key TEXT,
                api_base_url TEXT
            );
        """)
