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


# --- LLM connection-test endpoint ---

class LiteLLMTestRequest(BaseModel):
    provider: str
    api_key: str | None = None  # optional override; if absent, use stored


class LiteLLMTestResponse(BaseModel):
    ok: bool
    latency_ms: int | None = None
    error: str | None = None


@router.post("/llm/test")
async def test_litellm_provider(req: LiteLLMTestRequest) -> LiteLLMTestResponse:
    """Probe a LiteLLM sub-provider with a 1-token completion to verify the key/endpoint works."""
    import json
    import logging
    import re
    import time
    import litellm
    from app.services.llm.litellm import _PROVIDER_PREFIX

    logger = logging.getLogger(__name__)

    def _extract_bad_request_message(exc: Exception) -> str:
        """Pull the user-facing message out of a BadRequestError, redacting any leaked secrets."""
        raw = str(exc)
        # Redact common credential patterns defensively (sk-..., Bearer tokens).
        redacted = re.sub(r"sk-[A-Za-z0-9_\-]{6,}", "sk-***", raw)
        redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]{6,}", "Bearer ***", redacted)
        # Try to extract the inner JSON {"error": {"message": "..."}} that providers commonly emit.
        match = re.search(r"\{.*\}", redacted, flags=re.DOTALL)
        if match:
            try:
                payload = json.loads(match.group(0))
                msg = payload.get("error", {}).get("message")
                if isinstance(msg, str) and msg:
                    return msg[:500]
            except (ValueError, AttributeError):
                pass
        # Fallback: truncated raw message.
        return redacted[:500]

    if req.provider not in _ALLOWED_LITELLM_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"unknown provider: {req.provider}")

    settings = get_llm_settings()
    sub = settings.get("providers", {}).get("litellm", {}).get("providers", {}).get(req.provider, {})

    api_key = req.api_key or sub.get("api_key")
    model = sub.get("model")
    api_base_url = sub.get("api_base_url")

    if not model:
        return LiteLLMTestResponse(ok=False, error="no model configured")
    if req.provider not in ("ollama", "vllm", "custom") and not api_key:
        return LiteLLMTestResponse(ok=False, error="no API key configured")

    prefix = _PROVIDER_PREFIX.get(req.provider, "openai")
    model_str = f"{prefix}/{model}"
    effective_api_base = api_base_url if req.provider in ("ollama", "vllm", "custom") else None

    start = time.monotonic()
    try:
        await litellm.acompletion(
            model=model_str,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=1,
            api_key=api_key,
            api_base=effective_api_base,
            timeout=15.0,
        )
        return LiteLLMTestResponse(ok=True, latency_ms=int((time.monotonic() - start) * 1000))
    except litellm.AuthenticationError:
        logger.exception("LiteLLM test: auth failure for %s", req.provider)
        return LiteLLMTestResponse(ok=False, error="authentication failed (invalid API key)")
    except litellm.APIConnectionError:
        logger.exception("LiteLLM test: connection failure for %s", req.provider)
        return LiteLLMTestResponse(ok=False, error="connection failed (endpoint unreachable)")
    except litellm.NotFoundError:
        logger.exception("LiteLLM test: model not found for %s", req.provider)
        return LiteLLMTestResponse(ok=False, error="model not found")
    except litellm.BadRequestError as e:
        logger.exception("LiteLLM test: bad request for %s", req.provider)
        return LiteLLMTestResponse(ok=False, error=_extract_bad_request_message(e))
    except Exception as e:
        logger.exception("LiteLLM test: unexpected error for %s", req.provider)
        return LiteLLMTestResponse(ok=False, error=f"error ({type(e).__name__})")


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
