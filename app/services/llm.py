import asyncio
import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from claude_agent_sdk import query, ClaudeAgentOptions, AssistantMessage, TextBlock

from app.database import get_db
from app.services.keyframes import KeyFrame
from app.services.transcript import TranscriptResult

logger = logging.getLogger(__name__)


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
            return json.loads(stdout.decode())
        return {"loggedIn": False}
    except Exception:
        logger.exception("Failed to check auth status")
        return {"loggedIn": False}

DEFAULT_PROMPT = """\
You are summarizing a YouTube video. You will be given a timestamped transcript \
with keyframe images interleaved at their corresponding timestamps.

When you encounter a [KEYFRAME: filename] line, read that file to see what's \
shown on screen at that point in the video.

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
    model: str | None = None,
) -> SummaryResult:
    """Summarize a video using Claude via the Agent SDK."""
    settings = await get_llm_settings()
    system_prompt = custom_prompt or settings.get("custom_prompt") or DEFAULT_PROMPT

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
    parts.append(_build_interleaved_transcript(transcript, keyframes))

    user_prompt = "\n".join(parts)

    logger.info(
        "Sending to Claude: %d segments, %d keyframes",
        len(transcript.segments),
        len(keyframes),
    )

    # Configure Agent SDK - only allow Read tool for viewing keyframe images
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        allowed_tools=["Read"] if keyframes else [],
        model=model or settings.get("model") or "claude-sonnet-4-20250514",
    )

    raw_response = await _run_query(user_prompt, options)
    logger.info("Got response: %d chars", len(raw_response))

    return _parse_response(raw_response)


def _build_interleaved_transcript(
    transcript: TranscriptResult, keyframes: list[KeyFrame]
) -> str:
    """Build transcript grouped by keyframe boundaries.

    Each keyframe marks a section boundary. Transcript segments between two
    keyframes are merged into a single block. The result looks like:

        [KEYFRAME: frame1.png]
        [0:00 - 0:58] First section text merged together...

        [KEYFRAME: frame2.png]
        [0:58 - 1:55] Second section text merged together...
    """
    if not transcript.segments:
        return transcript.text

    if not keyframes:
        # No keyframes — just merge all segments with timestamps
        return _merge_segments(transcript.segments)

    # Sort keyframes by timestamp
    sorted_kf = sorted(keyframes, key=lambda kf: kf.timestamp)

    # Assign each segment to a keyframe group.
    # A segment belongs to the most recent keyframe at or before its start time.
    # Segments before the first keyframe go into group 0 (no keyframe header).
    groups: list[tuple[KeyFrame | None, list]] = []

    kf_idx = 0
    # Segments before the first keyframe
    pre_segments = []
    for seg in transcript.segments:
        # Advance keyframe index while next keyframe is at or before this segment
        while kf_idx < len(sorted_kf) and sorted_kf[kf_idx].timestamp <= seg.start:
            # Start a new group for this keyframe
            groups.append((sorted_kf[kf_idx], []))
            kf_idx += 1

        if groups:
            groups[-1][1].append(seg)
        else:
            pre_segments.append(seg)

    # Any remaining keyframes after the last segment
    while kf_idx < len(sorted_kf):
        groups.append((sorted_kf[kf_idx], []))
        kf_idx += 1

    # Build output
    blocks = []

    if pre_segments:
        blocks.append(_merge_segments(pre_segments))

    for kf, segments in groups:
        block_lines = []
        if kf:
            block_lines.append(f"[KEYFRAME: {kf.image_path}]")
        if segments:
            block_lines.append(_merge_segments(segments))
        if block_lines:
            blocks.append("\n".join(block_lines))

    return "\n\n".join(blocks)


def _merge_segments(segments: list) -> str:
    """Merge consecutive transcript segments into a single timestamped line."""
    if not segments:
        return ""
    start = _format_timestamp(segments[0].start)
    end = _format_timestamp(segments[-1].end)
    text = " ".join(seg.text for seg in segments)
    return f"[{start} - {end}] {text}"


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


def _format_duration(seconds: int) -> str:
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h{m:02d}m"
    return f"{m}m{s:02d}s"
