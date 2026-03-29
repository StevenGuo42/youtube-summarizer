from fastapi import APIRouter
from pydantic import BaseModel

from app.database import get_db
from app.services.llm import DEFAULT_PROMPT, get_auth_status

router = APIRouter()


@router.get("/auth/claude")
async def claude_auth_status():
    """Check if Claude authentication is configured."""
    return await get_auth_status()


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    custom_prompt: str | None = None


class LLMConfigResponse(BaseModel):
    model: str
    custom_prompt: str | None
    default_prompt: str


@router.get("/llm")
async def get_llm_config() -> LLMConfigResponse:
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM llm_settings WHERE id = 1")
        row = await row.fetchone()
        if row:
            return LLMConfigResponse(
                model=row["model"] or "claude-sonnet-4-20250514",
                custom_prompt=row["custom_prompt"],
                default_prompt=DEFAULT_PROMPT,
            )
        return LLMConfigResponse(
            model="claude-sonnet-4-20250514",
            custom_prompt=None,
            default_prompt=DEFAULT_PROMPT,
        )
    finally:
        await db.close()


@router.post("/llm")
async def save_llm_config(config: LLMConfig):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO llm_settings (id, model, custom_prompt)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 model = excluded.model,
                 custom_prompt = excluded.custom_prompt""",
            (config.model, config.custom_prompt),
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()


class WorkerConfig(BaseModel):
    processing_mode: str = "sequential"
    batch_size: int = 5


class WorkerConfigResponse(BaseModel):
    processing_mode: str
    batch_size: int


@router.get("/worker")
async def get_worker_config() -> WorkerConfigResponse:
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM worker_settings WHERE id = 1")
        row = await row.fetchone()
        if row:
            return WorkerConfigResponse(
                processing_mode=row["processing_mode"],
                batch_size=row["batch_size"],
            )
        return WorkerConfigResponse(processing_mode="sequential", batch_size=5)
    finally:
        await db.close()


@router.post("/worker")
async def save_worker_config(config: WorkerConfig):
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO worker_settings (id, processing_mode, batch_size)
               VALUES (1, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                 processing_mode = excluded.processing_mode,
                 batch_size = excluded.batch_size""",
            (config.processing_mode, config.batch_size),
        )
        await db.commit()
        return {"status": "ok"}
    finally:
        await db.close()
