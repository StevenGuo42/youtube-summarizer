import json
import logging
import re
from pathlib import Path

from app.services.keyframes import KeyFrame
from app.services.llm.base import KeyframeMode, SummaryResult
from app.services.transcript import TranscriptResult

logger = logging.getLogger(__name__)

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


def _build_codex_transcript(
    transcript: TranscriptResult,
    keyframes: list[KeyFrame],
    mode: KeyframeMode = KeyframeMode.IMAGE,
    ocr_paths: list[Path | None] | None = None,
    video_meta: dict | None = None,
    ocr_results: list | None = None,
) -> tuple[str, list]:
    """Variant of _build_interleaved_transcript for Codex.

    Returns (transcript_text, sorted_kf_with_images) where:
    - [KEYFRAME: path] markers are replaced with [KEYFRAME N] (1-based, matching -i order)
    - sorted_kf_with_images is the ordered list of image-bearing keyframes for -i flag construction

    For non-image modes, falls back to _build_interleaved_transcript output with empty list.
    """
    _IMAGE_MODES = (KeyframeMode.IMAGE, KeyframeMode.OCR_IMAGE, KeyframeMode.OCR_INLINE_IMAGE)

    if mode not in _IMAGE_MODES:
        return _build_interleaved_transcript(
            transcript, keyframes, mode=mode,
            ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
        ), []

    # Build the normal interleaved transcript first, then post-process markers
    # We need the sorted keyframe order to assign indices
    if not keyframes:
        return _build_interleaved_transcript(
            transcript, keyframes, mode=mode,
            ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
        ), []

    sorted_kf = sorted(keyframes, key=lambda kf: kf.timestamp)
    # Build index map: path -> 1-based index
    path_to_index = {str(kf.image_path): i + 1 for i, kf in enumerate(sorted_kf)}

    # Get the raw text using the standard builder
    raw = _build_interleaved_transcript(
        transcript, keyframes, mode=mode,
        ocr_paths=ocr_paths, video_meta=video_meta, ocr_results=ocr_results,
    )

    # Replace [KEYFRAME: /abs/path/to/frame.png] with [KEYFRAME N]
    def _replace_marker(m: re.Match) -> str:
        path = m.group(1)
        idx = path_to_index.get(path, "?")
        return f"[KEYFRAME {idx}]"

    result = re.sub(r"\[KEYFRAME: ([^\]]+)\]", _replace_marker, raw)
    return result, sorted_kf


def _merge_segments(segments: list) -> str:
    """Merge consecutive transcript segments into plain text."""
    if not segments:
        return ""
    return " ".join(seg.text for seg in segments)


def _parse_response(raw: str) -> SummaryResult:
    """Parse the JSON response from Claude into a SummaryResult."""
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
