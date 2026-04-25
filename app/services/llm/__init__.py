"""LLM backend abstraction package.

Public API surface — all names imported here maintain backward compatibility
with the former app/services/llm.py module.
"""
import logging

from app.services.llm.base import (
    KeyframeMode,
    LLMBackend,
    LLMBackendError,
    SummaryResult,
)
from app.services.llm.prompt import (
    DEFAULT_PROMPT,
    PROMPT_PLACEHOLDER,
    build_system_prompt,
    _build_codex_transcript,
    _build_interleaved_transcript,
    _format_duration,
    _format_timestamp,
    _merge_segments,
    _parse_response,
    _result_from_dict,
)
from app.services.llm.claude import ClaudeBackend
from app.services.llm.codex import CodexBackend
from app.services.llm.litellm import LiteLLMBackend

logger = logging.getLogger(__name__)

_BACKENDS = {
    "claude": ClaudeBackend,
    "codex": CodexBackend,
    "litellm": LiteLLMBackend,
}


def list_backends() -> list[str]:
    """Return list of available backend names."""
    return list(_BACKENDS.keys())


def get_active_backend() -> LLMBackend:
    """Instantiate and return the active backend based on settings.

    In Plan 01 (pre-migration), always returns ClaudeBackend.
    Plan 05 updates this to read active_provider from nested settings.
    """
    from app.settings import get_llm_settings
    settings = get_llm_settings()
    # After Plan 05, settings has active_provider key. Before, default to claude.
    provider = settings.get("active_provider", "claude")
    backend_cls = _BACKENDS.get(provider, ClaudeBackend)
    return backend_cls()


async def get_auth_status() -> dict:
    """Check Claude authentication status (backward-compatible wrapper).

    Returns same shape as old llm.py get_auth_status(). Plan 05 extends
    the router to call per-backend auth_status() directly.
    """
    return await ClaudeBackend().auth_status()


async def summarize(
    transcript,
    keyframes,
    video_meta: dict,
    custom_prompt: str | None = None,
    custom_prompt_mode: str = "replace",
    model: str | None = None,
    keyframe_mode: KeyframeMode = KeyframeMode.IMAGE,
    ocr_paths=None,
    ocr_results=None,
    output_language: str | None = None,
) -> SummaryResult:
    """Dispatch summarize() to the active backend.

    Signature is identical to the old llm.py summarize() — pipeline.py
    call sites need no changes (per D-04).
    """
    backend = get_active_backend()
    return await backend.summarize(
        transcript=transcript,
        keyframes=keyframes,
        video_meta=video_meta,
        custom_prompt=custom_prompt,
        custom_prompt_mode=custom_prompt_mode,
        model=model,
        keyframe_mode=keyframe_mode,
        ocr_paths=ocr_paths,
        ocr_results=ocr_results,
        output_language=output_language,
    )
