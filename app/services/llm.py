import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from app.database import get_db
from app.services.keyframes import KeyFrame
from app.services.transcript import TranscriptResult

logger = logging.getLogger(__name__)


class KeyframeMode(str, Enum):
    IMAGE = "image"
    OCR = "ocr"
    OCR_IMAGE = "ocr+image"
    OCR_INLINE = "ocr-inline"
    OCR_INLINE_IMAGE = "ocr-inline+image"
    NONE = "none"


def _get_cli_path() -> str:
    """Get path to the bundled claude CLI binary."""
    import claude_agent_sdk
    cli = Path(claude_agent_sdk.__file__).parent / "_bundled" / "claude"
    if cli.exists():
        return str(cli)
    # Fall back to system-installed claude
    system_claude = shutil.which("claude")
    if system_claude:
        return system_claude
    raise FileNotFoundError("Claude CLI not found")


async def get_auth_status() -> dict:
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
        logger.exception("Failed to check auth status")
        return {"loggedIn": False, "cli_error": True}

PROMPT_PLACEHOLDER = "{{CUSTOM_INSTRUCTIONS}}"

DEFAULT_PROMPT = """\
You are summarizing a YouTube video. You will be given a timestamped transcript \
with keyframe images interleaved at their corresponding timestamps.

When you encounter a [KEYFRAME: filename] line, read that file to see what's \
shown on screen at that point in the video.

{{CUSTOM_INSTRUCTIONS}}

Produce a summary in the following JSON format (and nothing else):

{
  "title": "Video title",
  "tldr": "A one-paragraph TL;DR of the video",
  "summary": "A detailed, well-structured summary in markdown format"
}

The summary field should use markdown headings, bullet points, and formatting \
to organize the content clearly. Include timestamps where relevant (e.g. [2:30]).

Return ONLY valid JSON, no other text.
"""


def build_system_prompt(
    custom_prompt: str | None,
    custom_prompt_mode: str = "replace",
    output_language: str | None = None,
) -> str:
    """Build the final system prompt based on mode.

    mode="replace": custom_prompt replaces the entire default prompt.
    mode="insert": custom_prompt is inserted at the {{CUSTOM_INSTRUCTIONS}} placeholder.
    """
    if not custom_prompt:
        prompt = DEFAULT_PROMPT.replace(PROMPT_PLACEHOLDER + "\n\n", "")
    elif custom_prompt_mode == "insert":
        prompt = DEFAULT_PROMPT.replace(PROMPT_PLACEHOLDER, custom_prompt)
    else:
        prompt = custom_prompt

    if output_language:
        prompt += f"\n\nYou MUST write your entire response in {output_language}."
    else:
        prompt += "\n\nYou MUST write your entire response in the same language as the transcript."

    return prompt


@dataclass
class SummaryResult:
    raw_response: str
    title: str
    tldr: str
    summary: str


async def get_llm_settings() -> dict:
    db = await get_db()
    try:
        row = await db.execute("SELECT * FROM llm_settings WHERE id = 1")
        row = await row.fetchone()
        if row:
            return dict(row)
        return {}
    finally:
        await db.close()


async def summarize(
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
    settings = await get_llm_settings()
    effective_prompt = custom_prompt or settings.get("custom_prompt")
    effective_mode = custom_prompt_mode if custom_prompt else (settings.get("custom_prompt_mode") or "replace")
    system_prompt = build_system_prompt(effective_prompt, effective_mode, output_language=output_language)

    # Build the user prompt with metadata
    parts = []
    parts.append(f"Video: {video_meta.get('title', 'Unknown')}")
    parts.append(f"Channel: {video_meta.get('channel', 'Unknown')}")
    duration = video_meta.get("duration")
    if duration:
        parts.append(f"Duration: {_format_duration(duration)}")
    parts.append("")
    parts.append("=== TRANSCRIPT ===")

    # Interleave keyframes into the timestamped transcript
    parts.append(_build_interleaved_transcript(
        transcript, keyframes, mode=keyframe_mode, ocr_paths=ocr_paths,
        video_meta=video_meta, ocr_results=ocr_results,
    ))

    user_prompt = "\n".join(parts)

    logger.info(
        "Sending to Claude: %d segments, %d keyframes, mode=%s",
        len(transcript.segments),
        len(keyframes),
        keyframe_mode.value,
    )

    # Enable Read tool only when Claude needs to read files
    needs_read = keyframe_mode in (
        KeyframeMode.IMAGE,
        KeyframeMode.OCR,
        KeyframeMode.OCR_IMAGE,
        KeyframeMode.OCR_INLINE_IMAGE,
    )

    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read"] if needs_read else [],
        model=model or settings.get("model") or "claude-sonnet-4-20250514",
    )

    raw_response = await _run_query(user_prompt, options)
    logger.info("Got response: %d chars", len(raw_response))

    return _parse_response(raw_response)


def _build_interleaved_transcript(
    transcript: TranscriptResult,
    keyframes: list[KeyFrame],
    mode: KeyframeMode = KeyframeMode.IMAGE,
    ocr_paths: list[Path | None] | None = None,
    video_meta: dict | None = None,
    ocr_results: list | None = None,
) -> str:
    """Build transcript grouped by keyframe boundaries with XML tags.

    Format per block:
        [start - end]
        [KEYFRAME: path.png]      (if mode includes images)
        [OCR: path.txt]           (if mode includes OCR files)
        <transcript>
        merged text...
        </transcript>
        <ocr_text>                (if mode includes inline OCR)
        ocr text...
        </ocr_text>
    """
    if not transcript.segments:
        return transcript.text

    if not keyframes or mode == KeyframeMode.NONE:
        text = _merge_segments(transcript.segments)
        return f"<transcript>\n{text}\n</transcript>"

    # Determine video end time for last keyframe range
    duration = None
    if video_meta:
        duration = video_meta.get("duration")
    if duration is None and transcript.segments:
        duration = transcript.segments[-1].end

    # Sort keyframes by timestamp
    sorted_kf = sorted(keyframes, key=lambda kf: kf.timestamp)

    # Build mappings from sorted index to OCR path / OCR result
    kf_to_idx = {id(kf): i for i, kf in enumerate(keyframes)}
    kf_ocr_paths: dict[int, Path | None] = {}
    kf_ocr_results: dict[int, object] = {}
    if ocr_paths:
        for i, kf in enumerate(sorted_kf):
            orig_idx = kf_to_idx[id(kf)]
            if orig_idx < len(ocr_paths):
                kf_ocr_paths[i] = ocr_paths[orig_idx]
    if ocr_results:
        for i, kf in enumerate(sorted_kf):
            orig_idx = kf_to_idx[id(kf)]
            if orig_idx < len(ocr_results):
                kf_ocr_results[i] = ocr_results[orig_idx]

    # Assign each segment to a keyframe group
    groups: list[tuple[int | None, KeyFrame | None, list]] = []

    kf_idx = 0
    pre_segments = []
    for seg in transcript.segments:
        while kf_idx < len(sorted_kf) and sorted_kf[kf_idx].timestamp <= seg.start:
            groups.append((kf_idx, sorted_kf[kf_idx], []))
            kf_idx += 1

        if groups:
            groups[-1][2].append(seg)
        else:
            pre_segments.append(seg)

    while kf_idx < len(sorted_kf):
        groups.append((kf_idx, sorted_kf[kf_idx], []))
        kf_idx += 1

    # Compute timestamp ranges
    all_starts = []
    if pre_segments:
        all_starts.append(0.0)
    for _, kf, _ in groups:
        all_starts.append(kf.timestamp if kf else 0.0)

    all_ends = []
    if pre_segments:
        all_ends.append(sorted_kf[0].timestamp if sorted_kf else (duration or 0))
    for g_idx, (_, kf, _) in enumerate(groups):
        if g_idx + 1 < len(groups):
            all_ends.append(groups[g_idx + 1][1].timestamp)
        else:
            all_ends.append(duration or (kf.timestamp if kf else 0))

    # Build output blocks
    blocks = []
    block_idx = 0

    if pre_segments:
        start_ts = _format_timestamp(all_starts[block_idx])
        end_ts = _format_timestamp(all_ends[block_idx])
        text = _merge_segments(pre_segments)
        block_lines = [f"[{start_ts} - {end_ts}]"]
        block_lines.append(f"<transcript>\n{text}\n</transcript>")
        blocks.append("\n".join(block_lines))
        block_idx += 1

    for idx, kf, segments in groups:
        start_ts = _format_timestamp(all_starts[block_idx])
        end_ts = _format_timestamp(all_ends[block_idx])
        block_lines = [f"[{start_ts} - {end_ts}]"]

        if kf:
            if mode in (KeyframeMode.IMAGE, KeyframeMode.OCR_IMAGE, KeyframeMode.OCR_INLINE_IMAGE):
                block_lines.append(f"[KEYFRAME: {kf.image_path}]")
            if mode in (KeyframeMode.OCR, KeyframeMode.OCR_IMAGE):
                ocr_path = kf_ocr_paths.get(idx)
                if ocr_path:
                    block_lines.append(f"[OCR: {ocr_path}]")

        if segments:
            text = _merge_segments(segments)
            block_lines.append(f"<transcript>\n{text}\n</transcript>")

        if mode in (KeyframeMode.OCR_INLINE, KeyframeMode.OCR_INLINE_IMAGE):
            ocr_r = kf_ocr_results.get(idx)
            if ocr_r and ocr_r.text:
                block_lines.append(f"<ocr_text>\n{ocr_r.text}\n</ocr_text>")

        blocks.append("\n".join(block_lines))
        block_idx += 1

    return "\n\n".join(blocks)


def _merge_segments(segments: list) -> str:
    """Merge consecutive transcript segments into plain text."""
    if not segments:
        return ""
    return " ".join(seg.text for seg in segments)


async def _run_query(prompt: str, options: ClaudeAgentOptions) -> str:
    """Run a query via the Agent SDK and collect the text response."""
    text_parts = []
    async for message in query(prompt=prompt, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
    return "\n".join(text_parts)


def _parse_response(raw: str) -> SummaryResult:
    """Parse the JSON response from Claude into a SummaryResult."""
    import re

    # Try direct JSON parse first
    text = raw.strip()
    try:
        data = json.loads(text)
        return _result_from_dict(raw, data)
    except json.JSONDecodeError:
        pass

    # Extract JSON from markdown code blocks (```json ... ``` or ``` ... ```)
    code_block = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if code_block:
        try:
            data = json.loads(code_block.group(1))
            return _result_from_dict(raw, data)
        except json.JSONDecodeError:
            pass

    # Try to find a JSON object anywhere in the text
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            data = json.loads(brace_match.group(0))
            return _result_from_dict(raw, data)
        except json.JSONDecodeError:
            pass

    logger.warning("Failed to parse JSON response, using raw text")
    return SummaryResult(raw_response=raw, title="", tldr="", summary=raw)


def _result_from_dict(raw: str, data: dict) -> SummaryResult:
    return SummaryResult(
        raw_response=raw,
        title=data.get("title", ""),
        tldr=data.get("tldr", ""),
        summary=data.get("summary", ""),
    )


def _format_timestamp(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _format_duration(seconds: int | float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"
