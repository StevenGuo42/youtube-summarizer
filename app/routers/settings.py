from fastapi import APIRouter
from pydantic import BaseModel

from app.services.llm import DEFAULT_PROMPT, PROMPT_PLACEHOLDER, get_auth_status
from app.settings import get_llm_settings, save_llm_settings, get_worker_settings, save_worker_settings

router = APIRouter()


@router.get("/auth/claude")
async def claude_auth_status():
    """Check if Claude authentication is configured."""
    return await get_auth_status()


class LLMConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    custom_prompt: str | None = None
    custom_prompt_mode: str = "replace"
    output_language: str | None = None


class LLMConfigResponse(BaseModel):
    model: str
    custom_prompt: str | None
    custom_prompt_mode: str
    output_language: str | None
    default_prompt: str
    prompt_placeholder: str


@router.get("/llm")
async def get_llm_config() -> LLMConfigResponse:
    settings = get_llm_settings()
    return LLMConfigResponse(
        model=settings.get("model") or "claude-sonnet-4-20250514",
        custom_prompt=settings.get("custom_prompt"),
        custom_prompt_mode=settings.get("custom_prompt_mode") or "replace",
        output_language=settings.get("output_language"),
        default_prompt=DEFAULT_PROMPT,
        prompt_placeholder=PROMPT_PLACEHOLDER,
    )


@router.post("/llm")
async def save_llm_config(config: LLMConfig):
    save_llm_settings(
        model=config.model,
        custom_prompt=config.custom_prompt,
        custom_prompt_mode=config.custom_prompt_mode,
        output_language=config.output_language,
    )
    return {"status": "ok"}


class WorkerConfig(BaseModel):
    processing_mode: str = "sequential"
    batch_size: int = 5


class WorkerConfigResponse(BaseModel):
    processing_mode: str
    batch_size: int


@router.get("/worker")
async def get_worker_config() -> WorkerConfigResponse:
    settings = get_worker_settings()
    return WorkerConfigResponse(
        processing_mode=settings["processing_mode"],
        batch_size=settings["batch_size"],
    )


@router.post("/worker")
async def save_worker_config(config: WorkerConfig):
    save_worker_settings(
        processing_mode=config.processing_mode,
        batch_size=config.batch_size,
    )
    return {"status": "ok"}
