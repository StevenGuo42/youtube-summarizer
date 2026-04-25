"""Codex backend — ChatGPT/OpenAI via the local `codex` CLI."""
import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path

from app.config import DATA_DIR, CODEX_SCHEMA_PATH, CODEX_MAX_IMAGE_FRAMES
from app.services.keyframes import KeyFrame
from app.services.llm.base import KeyframeMode, LLMBackend, LLMBackendError, SummaryResult
from app.services.llm.prompt import (
    build_system_prompt,
    _build_codex_transcript,
    _build_interleaved_transcript,
    _format_duration,
    _parse_response,
)
from app.services.transcript import TranscriptResult

logger = logging.getLogger(__name__)

_ALL_MODES = set(KeyframeMode)
_IMAGE_MODES = {KeyframeMode.IMAGE, KeyframeMode.OCR_IMAGE, KeyframeMode.OCR_INLINE_IMAGE}

# JSON Schema for --output-schema (OpenAI structured outputs requires additionalProperties: false)
_OUTPUT_SCHEMA = {
    "$schema": "http://json-schema.org/draft-07/schema#",
    "type": "object",
    "required": ["title", "tldr", "summary"],
    "additionalProperties": False,  # REQUIRED by OpenAI structured-outputs API
    "properties": {
        "title": {"type": "string"},
        "tldr": {"type": "string"},
        "summary": {"type": "string"},
    },
}


def _ensure_schema_file() -> Path:
    """Write the JSON Schema for --output-schema once; reuse on subsequent calls."""
    if CODEX_SCHEMA_PATH.exists():
        return CODEX_SCHEMA_PATH
    content = json.dumps(_OUTPUT_SCHEMA, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(dir=DATA_DIR, suffix=".json.tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            f.write(content)
        Path(tmp).replace(CODEX_SCHEMA_PATH)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
    logger.info("Wrote Codex output schema to %s", CODEX_SCHEMA_PATH)
    return CODEX_SCHEMA_PATH


def _log_codex_event(event: dict) -> None:
    """Log progress events from the Codex JSON event stream."""
    event_type = event.get("type", "")
    if event_type == "turn.completed":
        usage = event.get("usage", {})
        logger.info(
            "Codex turn completed: %d input tokens, %d output tokens",
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )
    elif event_type == "item.completed":
        item = event.get("item", {})
        if item.get("type") == "agent_message":
            logger.debug("Codex agent message item completed")
    elif event_type in ("thread.started", "turn.started"):
        logger.debug("Codex event: %s", event_type)


async def _run_codex(
    prompt: str,
    schema_path: Path,
    output_path: Path,
    image_paths: list[Path],
    model: str,
    timeout: float = 300.0,
) -> str:
    """Run codex exec and return the content of the output file."""
    cmd = [
        "codex", "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--sandbox", "read-only",
        "--output-schema", str(schema_path),
        "--json",
        "-o", str(output_path),
        "-m", model,
    ]
    for img in image_paths:
        cmd += ["-i", str(img)]

    logger.info(
        "Running codex exec: model=%s, images=%d, prompt_len=%d",
        model, len(image_paths), len(prompt),
    )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise LLMBackendError("Codex timed out after 300s")

    # Parse JSON event stream from stdout for progress logging
    for line in stdout.decode(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
            if event.get("type") == "turn.failed":
                err_msg = event.get("error", {}).get("message", "unknown error")
                raise LLMBackendError(f"Codex turn failed: {err_msg}")
            _log_codex_event(event)
        except json.JSONDecodeError:
            pass

    if proc.returncode != 0:
        err = stderr.decode(errors="replace").strip()
        raise LLMBackendError(f"Codex exited {proc.returncode}: {err}")

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise LLMBackendError("Codex produced no output file")

    return output_path.read_text(encoding="utf-8")


class CodexBackend(LLMBackend):

    def supported_modes(self) -> set[KeyframeMode]:
        """Codex supports all modes: images via -i flags, text via prompt."""
        return _ALL_MODES

    async def auth_status(self) -> dict:
        """Check Codex authentication status.

        IMPORTANT: `codex login status` writes to STDERR only. Stdout is empty.
        Parse the stderr string to determine login state.
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "codex", "login", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
            text = stderr.decode(errors="replace").strip()
            if text.startswith("Logged in"):
                return {"loggedIn": True, "method": text, "cli_error": False}
            elif text == "Not logged in":
                return {"loggedIn": False, "cli_error": False}
            else:
                logger.warning("Unexpected codex login status output: %r", text)
                return {"loggedIn": False, "cli_error": True}
        except asyncio.TimeoutError:
            logger.warning("Codex auth status check timed out")
            return {"loggedIn": False, "cli_error": True}
        except Exception:
            logger.exception("Failed to check Codex auth status")
            return {"loggedIn": False, "cli_error": True}

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
        """Summarize a video using the Codex CLI."""
        from app.settings import get_llm_settings
        settings = get_llm_settings()
        # Read from active provider config (Plan 05 sets nested shape; until then flat fallback)
        providers = settings.get("providers", {})
        codex_cfg = providers.get("codex", {})
        effective_model = model or codex_cfg.get("model") or "gpt-5.4"
        effective_prompt = custom_prompt or codex_cfg.get("custom_prompt")
        effective_mode = custom_prompt_mode if custom_prompt else (codex_cfg.get("custom_prompt_mode") or "replace")
        effective_language = output_language or codex_cfg.get("output_language")

        system_prompt = build_system_prompt(effective_prompt, effective_mode, output_language=effective_language)

        # Build user prompt content
        parts = []
        parts.append(f"Video: {video_meta.get('title', 'Unknown')}")
        parts.append(f"Channel: {video_meta.get('channel', 'Unknown')}")
        duration = video_meta.get("duration")
        if duration:
            parts.append(f"Duration: {_format_duration(duration)}")
        parts.append("")
        parts.append("=== TRANSCRIPT ===")

        # For image modes: use Codex index-marker variant
        image_paths: list[Path] = []
        if keyframe_mode in _IMAGE_MODES:
            transcript_text, sorted_kf = _build_codex_transcript(
                transcript, keyframes, mode=keyframe_mode,
                ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
            )
            # Apply hard cap: keep latest CODEX_MAX_IMAGE_FRAMES by timestamp
            if len(sorted_kf) > CODEX_MAX_IMAGE_FRAMES:
                logger.warning(
                    "Codex: %d keyframes exceeds cap %d; keeping latest %d",
                    len(sorted_kf), CODEX_MAX_IMAGE_FRAMES, CODEX_MAX_IMAGE_FRAMES,
                )
                sorted_kf = sorted_kf[-CODEX_MAX_IMAGE_FRAMES:]
            image_paths = [kf.image_path for kf in sorted_kf]
        else:
            transcript_text = _build_interleaved_transcript(
                transcript, keyframes, mode=keyframe_mode,
                ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
            )

        parts.append(transcript_text)
        user_prompt = "\n".join(parts)

        # Combine system + user into single stdin prompt for codex exec
        full_prompt = f"{system_prompt}\n\n{user_prompt}"

        logger.info(
            "Sending to Codex: model=%s, %d segments, %d images, mode=%s",
            effective_model, len(transcript.segments), len(image_paths), keyframe_mode.value,
        )

        schema_path = _ensure_schema_file()
        # Output file in DATA_DIR to avoid collision across concurrent job workers
        output_path = DATA_DIR / f"codex_out_{uuid.uuid4().hex[:8]}.txt"
        try:
            raw = await _run_codex(
                prompt=full_prompt,
                schema_path=schema_path,
                output_path=output_path,
                image_paths=image_paths,
                model=effective_model,
            )
        finally:
            output_path.unlink(missing_ok=True)

        logger.info("Got response from Codex: %d chars", len(raw))
        return _parse_response(raw)
