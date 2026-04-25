import asyncio
import json
import logging
import shutil
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from app.services.keyframes import KeyFrame
from app.services.llm.base import KeyframeMode, LLMBackend, LLMBackendError, SummaryResult
from app.services.llm.prompt import build_system_prompt, _build_interleaved_transcript, _parse_response
from app.services.transcript import TranscriptResult
from app.settings import get_llm_settings

logger = logging.getLogger(__name__)

_ALL_MODES = set(KeyframeMode)


def _get_cli_path() -> str:
    """Get path to the bundled claude CLI binary."""
    import claude_agent_sdk
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if cli.exists():
        return str(cli)
    system_claude = shutil.which("claude")
    if system_claude:
        return system_claude
    raise FileNotFoundError("Claude CLI not found")


async def _run_query(prompt: str, options: ClaudeAgentOptions) -> str:
    """Run a query via the Agent SDK and collect the text response."""
    text_parts = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return "\n".join(text_parts)


class ClaudeBackend(LLMBackend):
    async def auth_status(self) -> dict:
        """Check Claude authentication status via the bundled CLI."""
        try:
            cli = _get_cli_path()
            proc = await asyncio.create_subprocess_exec(
                cli, "auth", "status",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            if proc.returncode == 0:
                result = json.loads(stdout.decode())
                result["cli_error"] = False
                return result
            return {"loggedIn": False, "cli_error": False}
        except Exception:
            logger.exception("Failed to check Claude auth status")
            return {"loggedIn": False, "cli_error": True}

    def supported_modes(self) -> set[KeyframeMode]:
        """Claude supports all keyframe modes via the Read tool."""
        return _ALL_MODES

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
        """Summarize a video using Claude via the Agent SDK."""
        settings = get_llm_settings()
        # Read from flat settings (Plan 05 will update to nested shape)
        effective_prompt = custom_prompt or settings.get("custom_prompt")
        effective_mode = custom_prompt_mode if custom_prompt else (settings.get("custom_prompt_mode") or "replace")
        system_prompt = build_system_prompt(effective_prompt, effective_mode, output_language=output_language)

        parts = []
        parts.append(f"Video: {video_meta.get('title', 'Unknown')}")
        parts.append(f"Channel: {video_meta.get('channel', 'Unknown')}")
        duration = video_meta.get("duration")
        if duration:
            from app.services.llm.prompt import _format_duration
            parts.append(f"Duration: {_format_duration(duration)}")
        parts.append("")
        parts.append("=== TRANSCRIPT ===")
        parts.append(_build_interleaved_transcript(
            transcript, keyframes, mode=keyframe_mode, ocr_paths=ocr_paths,
            video_meta=video_meta, ocr_results=ocr_results,
        ))
        user_prompt = "\n".join(parts)

        logger.info(
            "Sending to Claude: %d segments, %d keyframes, mode=%s",
            len(transcript.segments), len(keyframes), keyframe_mode.value,
        )

        needs_read = keyframe_mode in (
            KeyframeMode.IMAGE, KeyframeMode.OCR,
            KeyframeMode.OCR_IMAGE, KeyframeMode.OCR_INLINE_IMAGE,
        )
        effective_model = model or settings.get("model") or "claude-sonnet-4-20250514"
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            allowed_tools=["Read"] if needs_read else [],
            model=effective_model,
        )
        raw_response = await _run_query(user_prompt, options)
        logger.info("Got response from Claude: %d chars", len(raw_response))
        return _parse_response(raw_response)
