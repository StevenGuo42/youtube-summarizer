from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.llm import DEFAULT_PROMPT, PROMPT_PLACEHOLDER, get_auth_status
from app.settings import (
    get_llm_settings,
    save_llm_settings,
    get_worker_settings,
    save_worker_settings,
    get_default_options,
    save_default_options,
    _mask_api_key,
)

router = APIRouter()

_ALLOWED_PROVIDERS = {"claude", "codex", "litellm"}
_ALLOWED_LITELLM_PROVIDERS = {"openai", "anthropic", "gemini", "ollama", "vllm", "custom"}


# --- Auth endpoints ---

@router.get("/auth/claude")
async def claude_auth_status():
    """Check if Claude authentication is configured."""
    return await get_auth_status()


@router.get("/auth/codex")
async def codex_auth_status():
    """Check Codex (ChatGPT) authentication status."""
    from app.services.llm.codex import CodexBackend
    return await CodexBackend().auth_status()


@router.get("/auth/litellm")
async def litellm_auth_status():
    """Report LiteLLM API key configuration status (no network call)."""
    from app.services.llm.litellm import LiteLLMBackend
    return await LiteLLMBackend().auth_status()


# --- LLM config endpoints ---

class ClaudeProviderConfig(BaseModel):
    model: str = "claude-sonnet-4-20250514"
    custom_prompt: str | None = None
    custom_prompt_mode: str = "replace"
    output_language: str | None = None


class CodexProviderConfig(BaseModel):
    model: str = "gpt-5.4"
    custom_prompt: str | None = None
    custom_prompt_mode: str = "replace"
    output_language: str | None = None


class LiteLLMSubProviderConfig(BaseModel):
    model: str = ""
    api_key: str | None = None
    api_base_url: str | None = None


class LiteLLMProviderConfig(BaseModel):
    active_litellm_provider: str = "openai"
    custom_prompt: str | None = None
    custom_prompt_mode: str = "replace"
    output_language: str | None = None
    providers: dict[str, LiteLLMSubProviderConfig] = Field(
        default_factory=lambda: {
            "openai":    LiteLLMSubProviderConfig(model="gpt-4o"),
            "anthropic": LiteLLMSubProviderConfig(model="claude-sonnet-4-20250514"),
            "gemini":    LiteLLMSubProviderConfig(model="gemini-2.5-flash"),
            "ollama":    LiteLLMSubProviderConfig(model="llama3", api_base_url="http://localhost:11434"),
            "vllm":      LiteLLMSubProviderConfig(model="", api_base_url="http://localhost:8000/v1"),
            "custom":    LiteLLMSubProviderConfig(model="", api_base_url=""),
        }
    )


class LLMProvidersConfig(BaseModel):
    claude: ClaudeProviderConfig = ClaudeProviderConfig()
    codex: CodexProviderConfig = CodexProviderConfig()
    litellm: LiteLLMProviderConfig = LiteLLMProviderConfig()


class LLMConfigRequest(BaseModel):
    active_provider: str = "claude"
    providers: LLMProvidersConfig = LLMProvidersConfig()


class LLMConfigResponse(BaseModel):
    active_provider: str
    providers: dict[str, Any]
    default_prompt: str
    prompt_placeholder: str


@router.get("/llm")
async def get_llm_config() -> LLMConfigResponse:
    settings = get_llm_settings()
    providers = settings.get("providers", {})

    response_providers: dict[str, Any] = {}
    for provider_name, cfg in providers.items():
        cfg_copy = dict(cfg)
        if provider_name == "litellm":
            # Mask each sub-provider's api_key independently
            sub_providers = cfg_copy.get("providers", {})
            masked_sub = {}
            for sub_name, sub_cfg in sub_providers.items():
                sub_copy = dict(sub_cfg)
                if "api_key" in sub_copy:
                    sub_copy["api_key"] = _mask_api_key(sub_copy["api_key"])
                masked_sub[sub_name] = sub_copy
            cfg_copy["providers"] = masked_sub
        response_providers[provider_name] = cfg_copy

    return LLMConfigResponse(
        active_provider=settings.get("active_provider", "claude"),
        providers=response_providers,
        default_prompt=DEFAULT_PROMPT,
        prompt_placeholder=PROMPT_PLACEHOLDER,
    )


@router.post("/llm")
async def save_llm_config(config: LLMConfigRequest):
    if config.active_provider not in _ALLOWED_PROVIDERS:
        raise HTTPException(status_code=422, detail=f"invalid provider: {config.active_provider}")

    litellm_active = config.providers.litellm.active_litellm_provider
    if litellm_active not in _ALLOWED_LITELLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"invalid litellm provider: {litellm_active}")

    providers_config = {
        "claude": config.providers.claude.model_dump(),
        "codex": config.providers.codex.model_dump(),
        "litellm": config.providers.litellm.model_dump(),
    }
    save_llm_settings(
        active_provider=config.active_provider,
        providers_config=providers_config,
    )
    return {"status": "ok"}


# --- Worker config endpoints (unchanged) ---

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


# --- Default options endpoints (unchanged) ---

class DefaultOptions(BaseModel):
    dedup_mode: str = "regular"
    keyframe_mode: str = "image"


_ALLOWED_DEDUP = {"regular", "slides", "ocr", "none"}
_ALLOWED_KEYFRAME = {"image", "ocr", "ocr+image", "ocr-inline", "ocr-inline+image", "none"}


@router.get("/defaults")
async def get_defaults() -> DefaultOptions:
    opts = get_default_options()
    return DefaultOptions(**opts)


@router.post("/defaults")
async def save_defaults(config: DefaultOptions):
    if config.dedup_mode not in _ALLOWED_DEDUP:
        raise HTTPException(status_code=422, detail=f"invalid dedup_mode: {config.dedup_mode}")
    if config.keyframe_mode not in _ALLOWED_KEYFRAME:
        raise HTTPException(status_code=422, detail=f"invalid keyframe_mode: {config.keyframe_mode}")
    save_default_options(
        dedup_mode=config.dedup_mode,
        keyframe_mode=config.keyframe_mode,
    )
    return {"status": "ok"}
