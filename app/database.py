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
                dedup_mode TEXT DEFAULT 'regular',
                keyframe_mode TEXT DEFAULT 'image',
                warnings TEXT,
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
                provider TEXT DEFAULT 'anthropic',
                model TEXT DEFAULT 'claude-sonnet-4-20250514',
                api_key TEXT,
                api_base_url TEXT,
                custom_prompt TEXT
            );

            CREATE TABLE IF NOT EXISTS worker_settings (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                processing_mode TEXT DEFAULT 'sequential',
                batch_size INTEGER DEFAULT 5
            );
        """)

        # Migrations for existing databases
        for col, default in [
            ("dedup_mode", "'regular'"),
            ("keyframe_mode", "'image'"),
            ("warnings", "NULL"),
            ("custom_prompt", "NULL"),
            ("custom_prompt_mode", "'replace'"),
            ("output_language", "NULL"),
            ("language", "NULL"),
        ]:
            try:
                await db.execute(f"ALTER TABLE jobs ADD COLUMN {col} TEXT DEFAULT {default}")
            except Exception:
                pass  # Column already exists

        # Migrations for llm_settings table
        try:
            await db.execute("ALTER TABLE llm_settings ADD COLUMN custom_prompt_mode TEXT DEFAULT 'replace'")
        except Exception:
            pass  # Column already exists

        try:
            await db.execute("ALTER TABLE llm_settings ADD COLUMN output_language TEXT DEFAULT NULL")
        except Exception:
            pass  # Column already exists

        try:
            await db.execute("ALTER TABLE jobs ADD COLUMN step_progress INTEGER DEFAULT NULL")
        except Exception:
            pass  # Column already exists

        try:
            await db.execute("ALTER TABLE jobs ADD COLUMN step_total INTEGER DEFAULT NULL")
        except Exception:
            pass  # Column already exists

        await db.commit()
