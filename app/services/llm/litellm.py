"""LiteLLM backend — multi-provider API-key-based escape hatch."""
import base64
import logging
import re
from pathlib import Path

import litellm

from app.config import LITELLM_MAX_IMAGE_FRAMES
from app.services.keyframes import KeyFrame
from app.services.llm.base import KeyframeMode, LLMBackend, LLMBackendError, SummaryResult
from app.services.llm.prompt import (
    build_system_prompt,
    _build_interleaved_transcript,
    _format_duration,
    _parse_response,
)
from app.services.transcript import TranscriptResult
from app.settings import get_llm_settings

logger = logging.getLogger(__name__)

_ALL_MODES = set(KeyframeMode)
_IMAGE_MODES = {KeyframeMode.IMAGE, KeyframeMode.OCR_IMAGE, KeyframeMode.OCR_INLINE_IMAGE}

# Provider → LiteLLM model string prefix mapping
# Source: RESEARCH.md §LiteLLM Integration Reference
_PROVIDER_PREFIX = {
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "ollama": "ollama_chat",   # Note: ollama_chat not ollama
    "custom": "openai",        # OpenAI-compatible endpoint
}


def _image_content_block(image_path: Path) -> dict:
    """Create a base64-encoded image_url content block for LiteLLM multimodal messages."""
    data = base64.b64encode(image_path.read_bytes()).decode()
    return {
        "type": "image_url",
        "image_url": {"url": f"data:image/png;base64,{data}"},
    }


def _select_evenly_spaced(keyframes: list[KeyFrame], max_count: int) -> list[KeyFrame]:
    """Select up to max_count keyframes, evenly spaced, to preserve temporal coverage."""
    if len(keyframes) <= max_count:
        return keyframes
    step = len(keyframes) / max_count
    return [keyframes[int(i * step)] for i in range(max_count)]


def _build_litellm_content(
    transcript: TranscriptResult,
    keyframes: list[KeyFrame],
    keyframe_mode: KeyframeMode,
    ocr_paths: list[Path | None] | None,
    ocr_results: list | None,
    video_meta: dict | None,
    image_kf: list[KeyFrame],
) -> list[dict] | str:
    """Build user message content (list of text+image blocks, or plain str for text modes).

    For image-bearing modes: interleave text blocks and image_url blocks.
    For text-only modes: return plain string from _build_interleaved_transcript.
    """
    if keyframe_mode not in _IMAGE_MODES or not image_kf:
        return _build_interleaved_transcript(
            transcript, keyframes, mode=keyframe_mode,
            ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
        )

    # Build a path-indexed image set for the selected keyframes
    selected_paths = {str(kf.image_path) for kf in image_kf}

    # Get interleaved transcript text blocks.
    # Strategy: build the full interleaved text, then split on [KEYFRAME: <path>] markers
    # and replace each with (text block + image content block).
    raw_text = _build_interleaved_transcript(
        transcript, keyframes, mode=keyframe_mode,
        ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
    )

    content: list[dict] = []
    # Split on [KEYFRAME: <path>] markers
    parts = re.split(r"(\[KEYFRAME: [^\]]+\])", raw_text)
    for part in parts:
        marker_match = re.match(r"\[KEYFRAME: ([^\]]+)\]", part)
        if marker_match:
            path = marker_match.group(1)
            if path in selected_paths:
                try:
                    content.append(_image_content_block(Path(path)))
                except (OSError, IOError) as e:
                    logger.warning("Could not read keyframe image %s: %s", path, e)
            # If path not in selected_paths (truncated), skip silently
        elif part.strip():
            content.append({"type": "text", "text": part})

    return content if content else raw_text


class LiteLLMBackend(LLMBackend):

    def supported_modes(self) -> set[KeyframeMode]:
        """LiteLLM supports all modes. Vision capability checked at call time via litellm.supports_vision()."""
        return _ALL_MODES

    async def auth_status(self) -> dict:
        """LiteLLM auth is an API key check — no network call needed.

        Returns {configured: bool, cli_error: False}.
        A key is configured if it is non-empty and not a masked value (starting with '...').
        """
        try:
            settings = get_llm_settings()
            providers = settings.get("providers", {})
            litellm_cfg = providers.get("litellm", {})
            api_key = litellm_cfg.get("api_key")
            # Key is genuinely configured if non-empty and not masked echo
            configured = bool(api_key and not api_key.startswith("..."))
            return {"configured": configured, "cli_error": False}
        except Exception:
            logger.exception("Failed to check LiteLLM auth status")
            return {"configured": False, "cli_error": True}

    async def summarize(
        self,
        transcript: TranscriptResult,
        keyframes: list[KeyFrame],
        video_meta: dict,
        custom_prompt: str | None = None,
        custom_prompt_mode: str = "replace",
        model: str | None = None,
        keyframe_mode: KeyframeMode = KeyframeMode.IMAGE,
        ocr_paths: list[Path | None] | None = None,
        ocr_results: list | None = None,
        output_language: str | None = None,
    ) -> SummaryResult:
        """Summarize a video using LiteLLM (multi-provider API-key gateway)."""
        settings = get_llm_settings()
        providers = settings.get("providers", {})
        litellm_cfg = providers.get("litellm", {})

        provider = litellm_cfg.get("provider", "openai")
        effective_model = model or litellm_cfg.get("model") or "gpt-4o"
        api_key = litellm_cfg.get("api_key") or None
        api_base_url = litellm_cfg.get("api_base_url") or None
        effective_prompt = custom_prompt or litellm_cfg.get("custom_prompt")
        effective_mode = custom_prompt_mode if custom_prompt else (litellm_cfg.get("custom_prompt_mode") or "replace")
        effective_language = output_language or litellm_cfg.get("output_language")

        system_prompt = build_system_prompt(effective_prompt, effective_mode, output_language=effective_language)

        # Build LiteLLM model string: "{prefix}/{model}"
        prefix = _PROVIDER_PREFIX.get(provider, "openai")
        model_str = f"{prefix}/{effective_model}"

        # Determine if vision is needed and supported
        use_images = keyframe_mode in _IMAGE_MODES and bool(keyframes)
        image_kf: list[KeyFrame] = []
        if use_images:
            try:
                vision_ok = litellm.supports_vision(model=model_str)
            except Exception:
                vision_ok = False  # unknown model — assume no vision
            if not vision_ok:
                logger.warning(
                    "LiteLLM: model %s does not support vision; falling back to ocr-inline",
                    model_str,
                )
                keyframe_mode = KeyframeMode.OCR_INLINE
                use_images = False

        if use_images:
            # Apply image frame cap (evenly spaced, not latest-N, for temporal coverage)
            sorted_kf = sorted(keyframes, key=lambda kf: kf.timestamp)
            if len(sorted_kf) > LITELLM_MAX_IMAGE_FRAMES:
                logger.warning(
                    "LiteLLM: %d keyframes exceeds cap %d; selecting %d evenly-spaced frames",
                    len(sorted_kf), LITELLM_MAX_IMAGE_FRAMES, LITELLM_MAX_IMAGE_FRAMES,
                )
                image_kf = _select_evenly_spaced(sorted_kf, LITELLM_MAX_IMAGE_FRAMES)
            else:
                image_kf = sorted_kf

        # Build the user content (text + image blocks or plain string)
        parts_prefix = [
            f"Video: {video_meta.get('title', 'Unknown')}",
            f"Channel: {video_meta.get('channel', 'Unknown')}",
        ]
        duration = video_meta.get("duration")
        if duration:
            parts_prefix.append(f"Duration: {_format_duration(duration)}")
        parts_prefix.extend(["", "=== TRANSCRIPT ==="])
        prefix_text = "\n".join(parts_prefix)

        content = _build_litellm_content(
            transcript=transcript, keyframes=keyframes,
            keyframe_mode=keyframe_mode,
            ocr_paths=ocr_paths, ocr_results=ocr_results,
            video_meta=video_meta, image_kf=image_kf,
        )

        # Prepend video metadata text to the content
        if isinstance(content, list):
            user_content: list[dict] | str = [{"type": "text", "text": prefix_text}] + content
        else:
            user_content = f"{prefix_text}\n{content}"

        logger.info(
            "Sending to LiteLLM: model=%s, %d segments, %d images, mode=%s",
            model_str, len(transcript.segments), len(image_kf), keyframe_mode.value,
        )

        # api_base: None for openai/anthropic/gemini; required for ollama/custom
        effective_api_base = api_base_url if provider in ("ollama", "custom") else None

        try:
            response = await litellm.acompletion(
                model=model_str,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                api_key=api_key,
                api_base=effective_api_base,
            )
            raw = response.choices[0].message.content
        except litellm.AuthenticationError as e:
            raise LLMBackendError(f"LiteLLM auth failed: {e}") from e
        except litellm.RateLimitError as e:
            raise LLMBackendError(f"LiteLLM rate limit: {e}") from e
        except litellm.APIConnectionError as e:
            raise LLMBackendError(f"LiteLLM connection error: {e}") from e
        except litellm.BadRequestError as e:
            raise LLMBackendError(f"LiteLLM bad request: {e}") from e
        except Exception as e:
            raise LLMBackendError(f"LiteLLM unexpected error: {e}") from e

        logger.info("Got response from LiteLLM: %d chars", len(raw))
        return _parse_response(raw)
